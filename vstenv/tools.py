"""Host tools, made independent of the host.

vstenv unpacks vendor packages with 7-Zip: NSIS and InstallAware setups, MSI
cabinets, Steinberg's RAR self-extractors (which need 7-Zip 24 or newer:
Ubuntu 24.04 ships 23.01, Debian's p7zip is 16.02, Fedora has 26). Rather
than depending on whatever a distribution has, a host 7-Zip is used only when
it is new enough; otherwise the official static Linux build is fetched from
7-Zip's release mirror into the app's data directory, exactly as the Wine
build is. The Flatpak bundles its own 7zz and never downloads one.

Cabinet files (the C runtime redistributables are a bootstrapper with
cabinets attached, nested one level deep) are located in Python by their MSCF
signature and handed to 7-Zip, so cabextract is not needed at all.
"""
from __future__ import annotations

import os, re, shutil, struct, subprocess, tarfile, tempfile
from pathlib import Path

from . import paths
from .download import fetch
from .progress import null_reporter

SEVEN_ZIP_VERSION = "26.03"
SEVEN_ZIP_URL = "https://github.com/ip7z/7zip/releases/download/26.03/7z2603-linux-x64.tar.xz"
SEVEN_ZIP_SHA256 = "dc99eff5008f1ab79bd7084c68513701547a808a89502bf4133683535ab3c695"
SEVEN_ZIP_MIN = (24, 0)          # RAR7 self-extractors
TOOLS = paths.DATA / "tools"
OWN = TOOLS / "7zz"

def seven_zip_version(exe: str | Path) -> tuple[int, int] | None:
    """(major, minor) from the banner of `7z`, `7zz`, `7za` or `7zzs`; None if it does not run."""
    try:
        out = subprocess.run([str(exe)], capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError): return None
    m = re.search(r"7-Zip(?: \([a-z]\))?(?: \[64\])? (\d+)\.(\d+)", out)
    return (int(m.group(1)), int(m.group(2))) if m else None

def _host_seven_zip() -> tuple[str, tuple[int, int]] | None:
    for name in ("7zz", "7z", "7zzs", "7za"):
        exe = shutil.which(name)
        if not exe: continue
        v = seven_zip_version(exe)
        if v and v >= SEVEN_ZIP_MIN: return exe, v
    return None

def seven_zip_status() -> dict:
    """{'path', 'version', 'source'} for the 7-Zip that would be used; path None if none is usable."""
    if OWN.exists():
        v = seven_zip_version(OWN)
        if v and v >= SEVEN_ZIP_MIN: return {"path": str(OWN), "version": "%d.%02d" % v, "source": "downloaded"}
    h = _host_seven_zip()
    if h: return {"path": h[0], "version": "%d.%02d" % h[1], "source": "host"}
    return {"path": None, "version": None, "source": None}

def install_seven_zip(reporter=None) -> Path:
    """Fetch the official static build into the app's tools directory."""
    r = null_reporter(reporter)
    if os.uname().machine not in ("x86_64", "amd64"):
        raise RuntimeError(f"no 7-Zip 24+ on this host and no static build for {os.uname().machine}: install 7-Zip (7zz) with your package manager")
    tar = fetch(SEVEN_ZIP_URL, paths.DOWNLOADS / Path(SEVEN_ZIP_URL).name, sha256=SEVEN_ZIP_SHA256, reporter=r, label=f"7-Zip {SEVEN_ZIP_VERSION}")
    TOOLS.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tar) as t:
        member = next((m for m in t.getmembers() if m.name in ("7zzs", "./7zzs")), None) or next(m for m in t.getmembers() if m.name.endswith("7zz"))
        with t.extractfile(member) as src, tempfile.NamedTemporaryFile(dir=TOOLS, delete=False) as dst:
            shutil.copyfileobj(src, dst); tmp = Path(dst.name)
    tmp.chmod(0o755); tmp.replace(OWN)
    if not seven_zip_version(OWN): raise RuntimeError("the downloaded 7-Zip does not run here")
    return OWN

def seven_zip(reporter=None) -> str:
    """Path of a usable 7-Zip, fetching one when the host has none new enough."""
    st = seven_zip_status()
    if st["path"]: return st["path"]
    r = null_reporter(reporter)
    r.step(f"Fetching 7-Zip {SEVEN_ZIP_VERSION} (the host has none new enough)")
    p = install_seven_zip(r); r.ok(str(p)); return str(p)

def ensure(reporter=None):
    """Setup step: make sure 7-Zip is available, and say which one."""
    r = null_reporter(reporter)
    r.step("7-Zip for unpacking vendor packages")
    st = seven_zip_status()
    if st["path"]: r.skip(f"{st['version']} ({st['source']})"); return
    p = install_seven_zip(r); r.ok(f"{SEVEN_ZIP_VERSION} (downloaded to {p})")

# --- cabinets --------------------------------------------------------------------
def cabinets(data: bytes):
    """Every Microsoft Cabinet embedded in `data`, by MSCF signature and header size."""
    i = 0
    while True:
        i = data.find(b"MSCF", i)
        if i < 0: return
        size = struct.unpack_from("<I", data, i + 8)[0] if i + 12 <= len(data) else 0
        if 36 <= size <= len(data) - i:                 # CFHEADER alone is 36 bytes
            yield data[i:i + size]; i += size
        else: i += 4

def extract_cabinets(container: Path, dest: Path, reporter=None) -> list[Path]:
    """Unpack every cabinet embedded in a file (a bootstrapper exe, or a cabinet
    itself), then every extracted payload that is itself a cabinet, one level
    down. Returns the extracted files."""
    exe = seven_zip(reporter); dest = Path(dest); dest.mkdir(parents=True, exist_ok=True); out: list[Path] = []
    def unpack(cab: bytes, into: Path):
        into.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(suffix=".cab", delete=False) as f: f.write(cab); name = f.name
        try: subprocess.run([exe, "x", "-y", f"-o{into}", name], check=True, capture_output=True, timeout=600)
        finally: Path(name).unlink(missing_ok=True)
        return [p for p in into.iterdir() if p.is_file()]
    for n, cab in enumerate(cabinets(Path(container).read_bytes())):
        for f in unpack(cab, dest / f"cab{n}"):
            with open(f, "rb") as h: nested = h.read(4) == b"MSCF"
            if nested: out += unpack(f.read_bytes(), dest / f"cab{n}" / (f.name + ".d"))
            else: out.append(f)
    return out
