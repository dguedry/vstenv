"""Windows runtime pieces every vendor's software needs in the prefix: real
fonts with substitutes for the Segoe family, and Microsoft's own C runtime in
place of Wine's builtin. Electron applications (Native Access, IK Product
Manager) and NI's installers all fail without them; nothing here is specific
to one vendor.
"""
import shutil, subprocess, tempfile
from pathlib import Path
from . import paths
from .download import fetch
from . import tools
from .progress import null_reporter
from .wine import Prefix
from . import fontalias, urlfilter

# winetricks ucrtbase2019 source: last VC2019 redist that still ships ucrtbase.dll
UCRT_URL = "https://download.visualstudio.microsoft.com/download/pr/85d47aa9-69ae-4162-8300-e6b7e4bf3cf3/52B196BBE9016488C735E7B41805B651261FFA5D7AA86EB6A1D0095BE83687B2/VC_redist.x64.exe"
UCRT_SHA256 = "52b196bbe9016488c735e7b41805b651261ffa5d7aa86eb6a1d0095be83687b2"
VC2022_URL = "https://aka.ms/vs/17/release/vc_redist.x64.exe"

def need(tool):
    if not shutil.which(tool): raise RuntimeError(f"missing host tool: {tool}")

# --- fonts + FontSubstitutes -------------------------------------------------------
# Only the four base faces per family: extra same-family faces (Condensed,
# ExtraLight...) poison Wine's font matching and re-trigger the GDI storm.
BASE_STYLES = {"regular", "book", "bold", "italic", "oblique", "bold italic", "bold oblique"}
FAMILIES = {  # family -> required?
    "DejaVu Sans": True, "DejaVu Sans Mono": True, "DejaVu Serif": True,
    "Liberation Sans": True, "Liberation Serif": True, "Liberation Mono": True,
    "Noto Sans Symbols": False, "Noto Sans Symbols2": False, "Noto Color Emoji": False,
}
PURGE = ("DejaVuSansCondensed", "DejaVuSans-ExtraLight", "DejaVuSansMono-Oblique", "DejaVuSansMono-BoldOblique",
         "DejaVuSerifCondensed", "Ubuntu-M", "Ubuntu-MI", "Ubuntu-L", "Ubuntu-LI", "Ubuntu-C", "Ubuntu-Th")
FONT_FALLBACK = {
    "DejaVu": ("https://github.com/dejavu-fonts/dejavu-fonts/releases/download/version_2_37/dejavu-fonts-ttf-2.37.zip", None),
    "Liberation": ("https://github.com/liberationfonts/liberation-fonts/files/7261482/liberation-fonts-ttf-2.1.5.tar.gz", None),
}

def host_fonts() -> dict[str, list[Path]]:
    """family -> base-style font files, via fontconfig."""
    found: dict[str, list[Path]] = {}
    try:
        out = subprocess.run(["fc-list", "--format", "%{file}|%{family}|%{style}\n"], capture_output=True, text=True, timeout=30).stdout
    except Exception: return found
    for line in out.splitlines():
        try: file, fams, styles = line.split("|", 2)
        except ValueError: continue
        fam_set = {f.strip() for f in fams.split(",")}
        style = styles.split(",")[0].strip().lower()
        for fam in FAMILIES:
            if fam in fam_set and file.lower().endswith((".ttf", ".otf")) and (style in BASE_STYLES or fam.startswith("Noto")):
                found.setdefault(fam, []).append(Path(file))
    return found

def _fallback_fonts(reporter, missing: set[str]) -> dict[str, list[Path]]:
    r = null_reporter(reporter); got = {}
    for vendor, (url, sha) in FONT_FALLBACK.items():
        fams = [f for f in missing if f.startswith(vendor)]
        if not fams: continue
        arc = fetch(url, paths.DOWNLOADS / Path(url).name, sha256=sha, reporter=r, label=vendor + " fonts")
        d = paths.DOWNLOADS / (vendor + "-fonts"); shutil.rmtree(d, ignore_errors=True); d.mkdir()
        if arc.suffix == ".zip":
            import zipfile; zipfile.ZipFile(arc).extractall(d)
        else:
            import tarfile; tarfile.open(arc).extractall(d, filter="tar")
        for ttf in d.rglob("*.ttf"):
            stem = ttf.stem
            if any(x in stem for x in PURGE) or "Condensed" in stem or "ExtraLight" in stem: continue
            fam = {"DejaVuSans": "DejaVu Sans", "DejaVuSansMono": "DejaVu Sans Mono", "DejaVuSerif": "DejaVu Serif",
                   "LiberationSans": "Liberation Sans", "LiberationSerif": "Liberation Serif", "LiberationMono": "Liberation Mono"}.get(stem.split("-")[0])
            if fam in fams: got.setdefault(fam, []).append(ttf)
    return got

def fonts(p: Prefix, reporter=None) -> dict:
    r = null_reporter(reporter)
    r.step("Installing fonts into the prefix")
    fdir = p.drive_c / "windows/Fonts"; fdir.mkdir(parents=True, exist_ok=True)
    have = host_fonts()
    missing = {f for f, req in FAMILIES.items() if f not in have}
    if missing & {f for f, req in FAMILIES.items() if req}:
        have.update(_fallback_fonts(r, missing))
    copied = 0
    for fam, files in have.items():
        for f in files:
            if any(x in f.stem for x in PURGE): continue
            dst = fdir / f.name
            if not dst.exists() or dst.stat().st_size != f.stat().st_size:
                shutil.copy2(f, dst); copied += 1
    for f in fdir.iterdir():
        if any(f.name.startswith(x) for x in PURGE): f.unlink()
    still = [f for f, req in FAMILIES.items() if req and f not in have]
    if still: r.fail(f"missing required fonts: {', '.join(still)}"); return {"missing": still}
    r.ok(f"{len(list(fdir.iterdir()))} fonts, {copied} new")
    return {"families": sorted(have), "missing": sorted(missing)}

def registry(p: Prefix, reporter=None):
    r = null_reporter(reporter)
    r.step("Font substitutes and DLL overrides")
    fdir = p.drive_c / "windows/Fonts"
    noto_sym = (fdir / "NotoSansSymbols-Regular.ttf").exists()
    noto_sym2 = (fdir / "NotoSansSymbols2-Regular.ttf").exists()
    noto_emoji = (fdir / "NotoColorEmoji.ttf").exists()
    # With the Segoe UI alias faces installed (fontalias), GDI uses them too: the
    # substitutes only cover what the alias lacks.
    segoe = fontalias.status(p)["installed"]
    subs = {
        "Segoe UI Black": "Segoe UI" if segoe else "DejaVu Sans",
    }
    aliased = ("Segoe UI", "Segoe UI Light", "Segoe UI Semibold", "Segoe UI Semilight")
    if not segoe: subs.update({k: "DejaVu Sans" for k in aliased})
    subs.update({
        "Segoe UI Symbol": "Noto Sans Symbols" if noto_sym else "DejaVu Sans",
        "Segoe UI Emoji": "Noto Color Emoji" if noto_emoji else "DejaVu Sans",
        "Segoe MDL2 Assets": "Noto Sans Symbols2" if noto_sym2 else "DejaVu Sans",
        "Segoe Fluent Icons": "Noto Sans Symbols2" if noto_sym2 else "DejaVu Sans",
        "Tahoma": "DejaVu Sans", "Verdana": "DejaVu Sans", "Microsoft Sans Serif": "DejaVu Sans",
        "Calibri": "Liberation Sans", "Cambria": "Liberation Serif", "Consolas": "DejaVu Sans Mono",
    })
    dlls = ["ucrtbase", "msvcp140", "msvcp140_1", "msvcp140_2", "msvcp140_atomic_wait", "msvcp140_codecvt_ids",
            "vcruntime140", "vcruntime140_1", "concrt140", "vcomp140"]
    reg = ["Windows Registry Editor Version 5.00", "",
           r"[HKEY_LOCAL_MACHINE\Software\Microsoft\Windows NT\CurrentVersion\FontSubstitutes]"]
    reg += [f'"{k}"="{v}"' for k, v in subs.items()]
    if segoe: reg += [f'"{k}"=-' for k in aliased]           # a substitute would hide the alias faces from GDI
    reg += ["", r"[HKEY_CURRENT_USER\Software\Wine\DllOverrides]"] + [f'"{d}"="native,builtin"' for d in dlls] + [""]
    # Wine's X11 driver asks the window manager for focus via WM_TAKE_FOCUS on
    # every activation (UseTakeFocus defaults to Y); with a plugin editor and a
    # DAW sharing the screen, each click on a plugin makes its Wine window raise
    # itself to the front. UseTakeFocus=N lets the window manager decide, which
    # stops the raise-on-click. (A per-plugin editor is embedded by the DAW, but
    # standalone and detached editor windows hit this.)
    reg += ["", r"[HKEY_CURRENT_USER\Software\Wine\X11 Driver]", '"UseTakeFocus"="N"', ""]
    rc = p.reg_import("\r\n".join(reg), "vstenv-setup.reg")
    if rc != 0: r.fail(f"regedit exit {rc}")
    else: r.ok()

# --- real C runtime -----------------------------------------------------------------------
def vc_runtime(p: Prefix, reporter=None):
    r = null_reporter(reporter)
    s32 = p.drive_c / "windows/system32"
    r.step("Installing real ucrtbase.dll (VC2019 redist)")
    if (s32 / "ucrtbase.dll.wine-builtin.bak").exists() and (s32 / "ucrtbase.dll").stat().st_size > 900_000:
        r.skip("present")
    else:
        vc19 = fetch(UCRT_URL, paths.DOWNLOADS / "VC_redist2019.x64.exe", sha256=UCRT_SHA256, reporter=r, label="VC2019")
        with tempfile.TemporaryDirectory() as t:
            files = tools.extract_cabinets(vc19, Path(t), r)          # bootstrapper -> attached cabinets -> a10 (a cabinet) -> ucrtbase.dll
            src = next((x for x in files if x.name.lower() == "ucrtbase.dll"), None)
            if src is None: raise RuntimeError("ucrtbase.dll not found in VC2019 redist")
            bak = s32 / "ucrtbase.dll.wine-builtin.bak"
            if not bak.exists() and (s32 / "ucrtbase.dll").exists(): shutil.copy2(s32 / "ucrtbase.dll", bak)
            shutil.copy2(src, s32 / "ucrtbase.dll")
        r.ok()
    r.step("Installing matched VC++ 2022 x64 runtime")
    if (s32 / "vcruntime140.dll.bak").exists() and (s32 / "msvcp140.dll.bak").exists():
        r.skip("present")
    else:
        vc22 = fetch(VC2022_URL, paths.DOWNLOADS / "VC_redist2022.x64.exe", reporter=r, label="VC2022")
        with tempfile.TemporaryDirectory() as t:
            files = tools.extract_cabinets(vc22, Path(t), r)
            x64 = [x for x in files if x.name.endswith("_amd64")]
            if not any(x.name == "vcruntime140.dll_amd64" for x in x64): raise RuntimeError("x64 runtime not found in VC2022 redist")
            n = 0
            for x in x64:
                dll = x.name[: -len("_amd64")]
                if (s32 / dll).exists() and not (s32 / (dll + ".bak")).exists(): shutil.copy2(s32 / dll, s32 / (dll + ".bak"))
                shutil.copy2(x, s32 / dll); n += 1
        r.ok(f"{n} DLLs")

def install(p: Prefix, reporter=None):
    fonts(p, reporter)
    try: fontalias.install(p, reporter)          # needs the network once; dialogs stay blank without it
    except Exception as e:
        r = null_reporter(reporter); r.step("Segoe UI font family for DirectWrite (dialog text)"); r.fail(str(e)[:100])
    vc_runtime(p, reporter); registry(p, reporter)
    try: urlfilter.install(p, reporter)
    except Exception as e:
        r = null_reporter(reporter); r.step("URL filter (drop vendor storefront tab-spam)"); r.fail(str(e)[:100])

def status(p: Prefix) -> dict:
    s32 = p.drive_c / "windows/system32"
    st = {"fonts": (p.drive_c / "windows/Fonts/DejaVuSans.ttf").exists(),
          "ucrtbase": (s32 / "ucrtbase.dll.wine-builtin.bak").exists(),
          "vc_runtime": (s32 / "vcruntime140.dll.bak").exists(),
          "segoe": fontalias.status(p)["installed"],
          "url_filter": urlfilter.installed(p)}
    st["prepared"] = st["fonts"] and st["vc_runtime"]
    return st
