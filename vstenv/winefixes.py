"""Patched Wine DLLs installed over the pinned Wine build.

The pinned Wine is used as upstream ships it, with one exception: DLLs that
vstenv patches because a vendor's programs need what Wine lacks. Today that is
dcomp.dll (DirectComposition). Steinberg's current products draw through it
(their graphics2d.dll: HALion Sonic 7, and the same library in Cubase and
Dorico of that generation); Wine's dcomp is stubs, and they abort at the first
CreateSurface. wine-staging carries a near-complete DirectComposition; the
patch in patches/wine/ adds what Steinberg still hit (surfaces created from
the device, virtual surfaces, a Direct2D device as rendering device). CI
builds the DLL from those sources (scripts/build-wine-fixes.sh) and attaches
wine-fixes-<wine version>.tar.gz to every release; this module installs it
into the Wine build's PE directory, keeping the original next to it as .orig.

The DLL lands in the build, not the prefix, so everything that runs this
Wine gets it: standalone programs and the yabridge plugin hosts alike.
"""
import hashlib, json, os, re, shutil, tarfile, tempfile
from datetime import date
from pathlib import Path
from . import paths, wine
from .download import fetch, text
from .progress import null_reporter

RELEASE_REPO = wine.RELEASE_REPO
PE_DIR = "lib/wine/x86_64-windows"
MARKER_NAME = "vstenv-wine-fixes.json"
FIXES = {"dcomp.dll": "DirectComposition (Steinberg's graphics2d: HALion Sonic 7, Cubase, Dorico)"}

def wine_version(build=None) -> str:
    """The pinned Wine's version number, from the build's directory name."""
    name = (build.root.name if build is not None else wine.WINE_BUILD["name"])
    m = re.search(r"wine-(\d+(?:\.\d+)*)", name)
    return m.group(1) if m else "unknown"

def asset_name(build=None) -> str: return f"wine-fixes-{wine_version(build)}.tar.gz"

def _sha(f: Path) -> str: return hashlib.sha256(f.read_bytes()).hexdigest()

def marker(build: wine.WineBuild) -> dict | None:
    try:
        f = build.root / MARKER_NAME
        return json.loads(f.read_text()) if f.exists() else None
    except (OSError, ValueError): return None

def installed(build: wine.WineBuild | None) -> dict[str, str]:
    """The patched DLLs actually in place: name -> sha256, per the marker and
    verified against the files (a re-extracted Wine silently puts the stock DLL back)."""
    if build is None: return {}
    m = marker(build) or {}
    out = {}
    for name, sha in (m.get("files") or {}).items():
        f = build.root / PE_DIR / name
        if f.exists() and _sha(f) == sha: out[name] = sha
    return out

def has(build: wine.WineBuild | None, dll: str) -> bool: return dll in installed(build)

def status(build: wine.WineBuild | None) -> dict:
    inst = installed(build)
    missing = [d for d in FIXES if d not in inst]
    return {"installed": not missing, "files": sorted(inst), "missing": missing, "asset": asset_name(build), "marker": marker(build) if build else None}

def _find_tarball(build, r) -> tuple[Path | None, str]:
    """VSTENV_WINE_FIXES (a local tarball, for builds made by hand), else the asset
    on the app's latest release."""
    override = os.environ.get("VSTENV_WINE_FIXES")
    if override:
        f = Path(override).expanduser()
        if f.is_file(): return f, f"{f.name} (VSTENV_WINE_FIXES)"
        r.log(f"VSTENV_WINE_FIXES={override} does not exist; ignoring")
    name = asset_name(build)
    try:
        rel = json.loads(text(f"https://api.github.com/repos/{RELEASE_REPO}/releases/latest"))
    except Exception as e:
        r.log(f"could not read {RELEASE_REPO} releases: {str(e)[:80]}"); return None, ""
    a = next((a for a in rel.get("assets", []) if a.get("name") == name), None)
    if a is None: return None, ""
    return fetch(a["browser_download_url"], paths.DOWNLOADS / name, reporter=r, label="wine fixes"), f"{name} ({RELEASE_REPO} release {rel.get('tag_name', '')})"

def install(build: wine.WineBuild, reporter=None, force=False) -> dict[str, str]:
    """Put the release's patched DLLs into the Wine build. Idempotent: nothing is
    touched when every file already has the manifest's hash. Returns what is installed."""
    r = null_reporter(reporter)
    r.step("Wine fixes (patched DLLs)")
    if not force and not status(build)["missing"]:
        r.skip(f"in place: {', '.join(sorted(installed(build)))}"); return installed(build)
    tgz, label = _find_tarball(build, r)
    if tgz is None:
        r.fail(f"no {asset_name(build)} on the latest {RELEASE_REPO} release (or offline); Steinberg's DirectComposition programs stay unavailable")
        return installed(build)
    with tempfile.TemporaryDirectory(prefix="wine-fixes-") as tmp:
        with tarfile.open(tgz) as t: t.extractall(tmp, filter="tar")
        src = next((d for d in Path(tmp).rglob("manifest.json")), None)
        if src is None: r.fail(f"{tgz.name} has no manifest.json"); return installed(build)
        man = json.loads(src.read_text()); src = src.parent
        if man.get("wine_version") != wine_version(build):
            r.fail(f"{tgz.name} is for Wine {man.get('wine_version')}, this build is {wine_version(build)}"); return installed(build)
        done = {}
        for name, sha in man.get("files", {}).items():
            f = src / name
            if not f.exists() or _sha(f) != sha: r.log(f"{name}: missing or hash mismatch in {tgz.name}; skipped"); continue
            dst = build.root / PE_DIR / name
            orig = dst.with_suffix(dst.suffix + ".orig")
            if dst.exists() and not orig.exists() and _sha(dst) != sha: shutil.copy2(dst, orig)
            tmpf = dst.with_name(f".{name}.new")
            shutil.copy2(f, tmpf); tmpf.replace(dst)      # rename: a running program keeps its old mapping
            done[name] = sha
    m = marker(build) or {}
    files = dict(m.get("files") or {}); files.update(done)
    (build.root / MARKER_NAME).write_text(json.dumps({"wine_version": wine_version(build), "files": files, "source": label,
                                                       "patches": man.get("patches", []), "installed": date.today().isoformat()}, indent=2))
    (r.ok if done else r.fail)(", ".join(f"{n} ({FIXES.get(n, 'patched')})" for n in done) if done else "nothing usable in the tarball")
    return installed(build)

def remove(build: wine.WineBuild, reporter=None) -> list[str]:
    """Put the stock DLLs back from their .orig copies."""
    r = null_reporter(reporter); r.step("Restoring stock Wine DLLs")
    back = []
    for name in list((marker(build) or {}).get("files") or {}):
        dst = build.root / PE_DIR / name; orig = dst.with_suffix(dst.suffix + ".orig")
        if orig.exists(): orig.replace(dst); back.append(name)
    (build.root / MARKER_NAME).unlink(missing_ok=True)
    r.ok(", ".join(back) if back else "nothing to restore")
    return back
