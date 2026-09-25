"""Steinberg installer packages: a zip (products, most runtime components) or a
RAR self-extractor (Install Assistant) holding `<Title> <version>/setup.xml`, a
.NET SetupBootstrapper (Setup.exe), one or more MSIs and, often, a signed
PowerShell "prerun" script.

The Download Assistant does not run these itself: it hands them to the
Steinberg Install Assistant, which verifies the prerun script's Authenticode
signature through WinVerifyTrust before anything else. Wine has no signature
provider for PowerShell scripts, so every package with a prerun script fails
with "preinstall.ps1: not trusted" (code 231) and the product is not
installed. The MSIs themselves install fine with msiexec, and the prerun
scripts seen so far only stop running Steinberg processes before an update.
So a failed package is finished here: extract, read setup.xml, msiexec each
MSI silently. The Download Assistant leaves the package and a status file
(`Steinberg_<Artifact>_Installer.json`, success false) in a folder under the
user's Temp; that is how staged installs are found.
"""
from __future__ import annotations

import json, re, shutil, subprocess, tempfile, zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from .. import paths
from ..progress import null_reporter
from ..wine import Prefix

STATUS_RE = re.compile(r"^Steinberg_(.+?)_Installer\.json$", re.I)

def _token(name: str) -> str:
    """'Steinberg_MediaBay_Installer' / 'MediaBay_Installer_win64.zip' -> 'mediabay'."""
    s = re.sub(r"\.(zip|exe|json)$", "", name, flags=re.I)
    s = re.sub(r"^steinberg[_ -]?", "", s, flags=re.I)
    s = re.sub(r"[_ -]?installer.*$", "", s, flags=re.I)
    s = re.sub(r"[_ -]?win(64)?$", "", s, flags=re.I)
    return re.sub(r"[^a-z0-9]", "", s.lower())

def _listing(f: Path) -> list[str]:
    f = Path(f)
    if f.suffix.lower() == ".zip":
        try:
            with zipfile.ZipFile(f) as z: return z.namelist()
        except (OSError, zipfile.BadZipFile): return []
    if f.suffix.lower() == ".exe" and shutil.which("7z"):
        try:
            cp = subprocess.run(["7z", "l", "-ba", "-slt", str(f)], capture_output=True, text=True, timeout=120)
            return [l[7:] for l in cp.stdout.splitlines() if l.startswith("Path = ")]
        except (OSError, subprocess.SubprocessError): return []
    return []

def is_package(f: Path) -> bool:
    """A Steinberg package: an archive with `<dir>/setup.xml` in it."""
    return any(n.lower().endswith("/setup.xml") or n.lower() == "setup.xml" for n in _listing(Path(f)))

def extract(pkg: Path, dest: Path) -> Path:
    """Unpack the package; returns the folder holding setup.xml."""
    pkg = Path(pkg); dest.mkdir(parents=True, exist_ok=True)
    if pkg.suffix.lower() == ".zip":
        with zipfile.ZipFile(pkg) as z: z.extractall(dest)
    else:
        if not shutil.which("7z"): raise RuntimeError("missing host tool: 7z")
        cp = subprocess.run(["7z", "x", "-y", f"-o{dest}", str(pkg)], capture_output=True, text=True, timeout=1800)
        if cp.returncode != 0:
            raise RuntimeError(f"7z could not unpack {pkg.name} (this self-extractor needs 7-Zip 24 or newer): "
                               + (cp.stdout + cp.stderr).strip().splitlines()[-1][:120])
    for x in sorted(dest.rglob("setup.xml")): return x.parent
    raise RuntimeError(f"{pkg.name} has no setup.xml")

@dataclass
class Setup:
    title: str
    msis: list[str] = field(default_factory=list)       # relative to the setup folder, product MSI first
    prerun: list[str] = field(default_factory=list)
    library: list[dict] = field(default_factory=list)   # <libraryManager> entries (VST Sound content), not handled yet

def parse_setup(xml_path: Path) -> Setup:
    root = ET.fromstring(Path(xml_path).read_text(encoding="utf-8", errors="replace"))
    product = root.find("product")
    title = (product.findtext("title") if product is not None else None) or Path(xml_path).parent.name
    msis: list[str] = []
    if product is not None and product.get("file"): msis.append(product.get("file"))
    search = [inc.get("msi") for inc in root.findall("include/path") if inc.get("msi")]
    for pk in root.findall("msiPackage"):
        f = pk.get("file")
        if not f or f in msis: continue
        msis.append(f)
    prerun = [x.get("prerun") for x in root.findall("include/prerun") if x.get("prerun")]
    library = [dict(x.attrib) for x in root.findall("libraryManager")]
    s = Setup(title=title, msis=msis, prerun=prerun, library=library)
    s.search = search  # type: ignore[attr-defined]
    return s

def _find_msi(setup_dir: Path, name: str, search: list[str]) -> Path | None:
    for d in [Path("."), *[Path(s) for s in search]]:
        c = setup_dir / d / name
        if c.is_file(): return c
    for c in setup_dir.rglob(name): return c
    return None

def _vtuple(v: str) -> tuple: return tuple(int(x) for x in re.findall(r"\d+", v or "")[:4])

def _already_newer(p: Prefix, msi: Path) -> str | None:
    """'name version' of an installed program that is the same product at the same
    or a newer version than this MSI (packages bundle old copies of shared
    components such as the Library Manager), else None."""
    try:
        from ..msi import Msi
        from .. import programs
        props = {r["Property"]: r["Value"] for r in Msi(msi).rows("Property")}
        name, ver = props.get("ProductName", ""), props.get("ProductVersion", "")
        if not name: return None
        for x in programs.installed(p):
            if x.name.lower() == name.lower() and _vtuple(x.version) >= _vtuple(ver): return f"{x.name} {x.version}"
    except Exception: return None
    return None

def install(p: Prefix, pkg: Path, reporter=None) -> dict:
    """Extract a package and install its MSIs silently. Returns a summary dict."""
    r = null_reporter(reporter); paths.ensure_dirs()
    work = Path(tempfile.mkdtemp(prefix="vstenv-steinberg-", dir=paths.CACHE))
    try:
        r.step(f"Unpacking {Path(pkg).name}")
        d = extract(Path(pkg), work); s = parse_setup(d / "setup.xml"); r.ok(s.title)
        if s.prerun: r.log(f"{s.title}: skipping prerun script(s) {', '.join(s.prerun)} (only stop running Steinberg processes)")
        done, failed = [], []
        for name in s.msis:
            msi = _find_msi(d, name, getattr(s, "search", []))
            r.step(f"Installing {name}")
            if msi is None: r.fail("not in the package"); failed.append(name); continue
            have = _already_newer(p, msi)
            if have: r.skip(f"{have} is already installed"); done.append(name); continue
            log = p.drive_c / f"vstenv-steinberg-{Path(name).stem}.log"; log.unlink(missing_ok=True)
            cp = p.run(["msiexec", "/i", str(msi), "/qn", "/l*v", p.to_win(log)], timeout=3600, cwd=str(msi.parent))
            if cp.returncode in (0, 3010, 1638):          # ok, ok-needs-reboot, same-or-newer already installed
                r.ok("installed" if cp.returncode != 1638 else "already installed"); done.append(name); log.unlink(missing_ok=True)
            else:
                r.fail(f"msiexec exit {cp.returncode} (log: {log.name})"); failed.append(name)
        if s.library: r.log(f"{s.title}: {len(s.library)} VST Sound content entries are left for the Steinberg Library Manager")
        return {"name": s.title, "method": "msi", "installed": done, "failed": failed}
    finally:
        shutil.rmtree(work, ignore_errors=True)

@dataclass
class Staged:
    name: str            # artifact, e.g. "MediaBay"
    dir: Path
    package: Path
    status: dict

def staged(p: Prefix) -> list[Staged]:
    """Packages the Download Assistant handed to the Install Assistant that
    did not install, still sitting in its Temp folders with their status file."""
    out: list[Staged] = []
    try: temp = p.user_dir / "AppData/Local/Temp"
    except OSError: return out
    if not temp.is_dir(): return out
    for js in sorted(temp.glob("*/Steinberg_*_Installer.json")):
        try: st = json.loads(js.read_text(errors="replace"))
        except (OSError, ValueError): continue
        if st.get("success") is not False: continue
        tok = _token(js.name)
        pkgs = [f for f in js.parent.iterdir() if f.suffix.lower() in (".zip", ".exe") and _token(f.name) == tok]
        if not pkgs: continue
        out.append(Staged(STATUS_RE.match(js.name).group(1).replace("_", " "), js.parent, pkgs[0], st))
    return out

def finish_staged(p: Prefix, reporter=None) -> list[dict]:
    r = null_reporter(reporter); results = []
    for st in staged(p):
        errs = "; ".join(f"{e.get('package', '')}: {e.get('message', '')}".strip(": ") for e in st.status.get("errors", []))
        if errs: r.log(f"{st.name}: the Install Assistant reported: {errs}")
        try:
            res = install(p, st.package, r)
            if not res["failed"]:
                st.status.update({"success": True, "finishedBy": "vstenv"})
                try: (st.dir / f"Steinberg_{st.name.replace(' ', '_')}_Installer.json").write_text(json.dumps(st.status, indent=2))
                except OSError: pass
            results.append(res)
        except Exception as e:
            r.step(f"Finishing {st.name}"); r.fail(str(e)[:140])
    return results
