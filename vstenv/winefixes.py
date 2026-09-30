"""Patched Wine DLLs installed over the pinned Wine build.

The pinned Wine is used as upstream ships it, with a few exceptions: DLLs that
vstenv patches because a vendor's programs need what Wine lacks (the patches
live in patches/wine/, with a README). Today:

- dcomp.dll: DirectComposition. Steinberg's current products draw through it
  (their graphics2d.dll: HALion Sonic 7, and the same library in Cubase and
  Dorico of that generation); Wine's dcomp is stubs and they abort at start.
- advapi32.dll: Credential Manager attributes. Steinberg's License Engine
  stores its sign-in (the refresh token) with a credential attribute; Wine
  dropped attributes, so every restart of the engine found an "old format"
  token, reset it, and the user was "signed out automatically".

CI builds the DLLs from the Wine sources with those patches
(scripts/build-wine-fixes.sh) and attaches wine-fixes-<wine version>.tar.gz to
every release; this module installs them into the Wine build's PE directory,
keeping the originals next to them as .orig. Wine loads a builtin DLL from the
build directory even though the prefix's system32 holds a copy, so nothing in
the prefix needs touching.

The DLLs land in the build, not the prefix, so everything that runs this
Wine gets them: standalone programs and the yabridge plugin hosts alike.
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
FIXES = {"dcomp.dll": "DirectComposition (Steinberg's VSTGUI: HALion Sonic 7, Cubase, Dorico; JUCE 8 GUIs: Spitfire Audio)",
         "dxgi.dll": "WaitForVBlank paced to the display refresh (JUCE 8 GUIs hang on Wine's E_NOTIMPL)",
         "advapi32.dll": "credential attributes (Steinberg's License Engine keeps its sign-in)",
         "ole32.dll": "drag-drop teardown survives a stale target (WebView2 apps crashed at startup)"}

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

def _release(r) -> tuple[dict | None, str]:
    """The app's latest release (its JSON) and tag, or (None, '') when offline."""
    try:
        rel = json.loads(text(f"https://api.github.com/repos/{RELEASE_REPO}/releases/latest"))
        return rel, rel.get("tag_name", "")
    except Exception as e:
        r.log(f"could not read {RELEASE_REPO} releases: {str(e)[:80]}"); return None, ""

def _locate(build, r) -> tuple[Path | None, dict | None, str, str]:
    """Where the fixes come from: VSTENV_WINE_FIXES (a local tarball, for builds made
    by hand) as a path, else the asset on the app's latest release, not yet fetched.
    Returns (local path, release asset, label, release tag)."""
    override = os.environ.get("VSTENV_WINE_FIXES")
    if override:
        f = Path(override).expanduser()
        if f.is_file(): return f, None, f"{f.name} (VSTENV_WINE_FIXES)", "local"
        r.log(f"VSTENV_WINE_FIXES={override} does not exist; ignoring")
    name = asset_name(build)
    rel, tag = _release(r)
    if rel is None: return None, None, "", ""
    a = next((a for a in rel.get("assets", []) if a.get("name") == name), None)
    return None, a, f"{name} ({RELEASE_REPO} release {tag})" if a else "", tag

def install(build: wine.WineBuild, reporter=None, force=False) -> dict[str, str]:
    """Put the release's patched DLLs into the Wine build. Idempotent: nothing is
    touched when the installed set came from the latest release (or, offline, when
    every file is in place). Returns what is installed."""
    r = null_reporter(reporter)
    r.step("Wine fixes (patched DLLs)")
    tgz, asset, label, tag = _locate(build, r)
    in_place = not status(build)["missing"]
    current = (marker(build) or {}).get("release")
    available = tgz is not None or asset is not None
    if not force and in_place and (not available or (tag and tag == current)):
        r.skip(f"in place: {', '.join(sorted(installed(build)))}" + (f" ({current})" if current else "")); return installed(build)
    if not available:
        r.fail(f"no {asset_name(build)} on the latest {RELEASE_REPO} release (or offline); Steinberg's DirectComposition programs stay unavailable")
        return installed(build)
    if tgz is None:   # cached per release: the same asset name carries a different build on every release
        tgz = fetch(asset["browser_download_url"], paths.DOWNLOADS / f"{tag}-{asset_name(build)}", reporter=r, label="wine fixes")
    with tempfile.TemporaryDirectory(prefix="wine-fixes-") as tmp:
        with tarfile.open(tgz) as t: t.extractall(tmp, filter="tar")
        src = next((d for d in Path(tmp).rglob("manifest.json")), None)
        if src is None: r.fail(f"{tgz.name} has no manifest.json"); return installed(build)
        man = json.loads(src.read_text()); src = src.parent
        if man.get("wine_version") != wine_version(build):
            r.fail(f"{tgz.name} is for Wine {man.get('wine_version')}, this build is {wine_version(build)}"); return installed(build)
        done = {}
        try:
            for name, sha in man.get("files", {}).items():
                f = src / name
                if not f.exists() or _sha(f) != sha: r.log(f"{name}: missing or hash mismatch in {tgz.name}; skipped"); continue
                dst = build.root / PE_DIR / name
                orig = dst.with_suffix(dst.suffix + ".orig")
                if dst.exists() and not orig.exists() and _sha(dst) != sha: shutil.copy2(dst, orig)
                if dst.exists() and _sha(dst) == sha: done[name] = sha; continue
                tmpf = dst.with_name(f".{name}.new")
                shutil.copy2(f, tmpf); tmpf.replace(dst)      # rename: a running program keeps its old mapping
                done[name] = sha
        finally:   # record what did land even if a later copy failed (disk full): status() must not deny a DLL that is in place
            m = marker(build) or {}
            files = dict(m.get("files") or {}); files.update(done)
            (build.root / MARKER_NAME).write_text(json.dumps({"wine_version": wine_version(build), "files": files, "source": label, "release": tag,
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
