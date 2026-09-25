"""Bridge the prefix's Windows plugins to Linux DAWs with yabridge.

yabridge runs plugins with the `wine` on PATH (or WINELOADER). Its host
launcher, which the app installs, is taught to run a prefix with the wine the
prefix records, so prefix and plugin host can never drift apart while every
other prefix keeps its own wine (see "which wine runs the plugin host").
Vendor modules add their own plugin directories (Vendor.plugin_dirs).
"""
import json, os, re, shutil, subprocess, tarfile
from pathlib import Path
from . import paths
from .download import fetch, text
from .progress import null_reporter
from .wine import Prefix

YAB_DIR = Path.home() / ".local/share/yabridge"
YCTL = YAB_DIR / "yabridgectl"
STANDARD_DIRS = [
    "Program Files/VstPlugins",
    "Program Files/Steinberg/VstPlugins",
    "Program Files/Common Files/VST2",
    "Program Files/Common Files/Steinberg/VST2",
    "Program Files/Common Files/VST3",
    "Program Files/Common Files/CLAP",
]
BROAD = ("", "Program Files", "Program Files/Common Files", "Program Files (x86)")

def _yctl_env(p: Prefix) -> dict:
    """yabridgectl env: our wine first on PATH; config in the *host* ~/.config so
    a yabridgectl run outside the sandbox sees the same plugin dirs."""
    e = p.env()
    # Inside Flatpak XDG_* are remapped to ~/.var/app/<id>/...; yabridgectl must see
    # the host locations (its libs in ~/.local/share/yabridge, config in ~/.config)
    # because the DAW-side yabridge on the host reads the same places.
    e["XDG_CONFIG_HOME"] = str(Path.home() / ".config"); e["XDG_DATA_HOME"] = str(Path.home() / ".local/share")
    return e

def installed() -> str | None:
    if not YCTL.exists(): return None
    if (m := build_marker()):
        return f"{m.get('yabridge_commit', '?')} (git {m.get('yabridge_ref', 'master')}, built for wine {m.get('wine_version', '?')})"
    try: return subprocess.run([str(YCTL), "--version"], capture_output=True, text=True, timeout=20).stdout.strip()
    except Exception: return "unknown"

# --- which yabridge works with which Wine ------------------------------------------------------
# yabridge's last release (5.1.1, Nov 2024) predates the window-management changes
# in Wine 9.22: with newer Wine, mouse clicks in bridged plugin GUIs land in the
# wrong place, in every DAW. The fix lives in yabridge's master branch, so CI
# builds master against its pinned Wine (scripts/build-yabridge.sh, published with
# each release) and installs that instead of the upstream release. nilinux's
# releases carry the same build for the same pinned Wine, so they are a fallback.
RELEASE_REPOS = ("dguedry/vstenv", "dguedry/nilinux")
MARKER = YAB_DIR / "vstenv-build.json"
LEGACY_MARKER_NAME = "nilinux-build.json"     # the same tarball, installed by nilinux
WINE_NEEDS_MASTER = (9, 22)

def build_marker() -> dict | None:
    """Metadata of a CI-built yabridge, or None for an upstream release."""
    for m in (MARKER, MARKER.with_name(LEGACY_MARKER_NAME)):
        try:
            if m.exists(): return json.loads(m.read_text(errors="replace"))
        except (OSError, ValueError): return None
    return None

def pinned_wine_version() -> str:
    from .wine import WINE_BUILD
    m = re.search(r"[0-9]+(?:\.[0-9]+)+", WINE_BUILD["name"])
    return m.group(0) if m else ""

def _vtuple(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v)[:3])

def needs_master(wine_version: str) -> bool:
    return bool(wine_version) and _vtuple(wine_version) >= WINE_NEEDS_MASTER

def pick_asset(assets: list[dict], wine_version: str) -> dict | None:
    """The nilinux-built yabridge tarball for exactly this Wine version, from a
    GitHub release's asset list ([{'name', 'browser_download_url'}, ...])."""
    pat = re.compile(rf"^yabridge-[0-9a-f]+-wine-{re.escape(wine_version)}\.tar\.gz$")
    for a in assets:
        if pat.match(a.get("name", "")): return a
    return None

def compatibility() -> tuple[bool, str]:
    """(ok, detail) for the installed yabridge against the pinned Wine."""
    if not YCTL.exists(): return False, "not installed"
    pinned = pinned_wine_version()
    if (m := build_marker()):
        if pinned and str(m.get("wine_version", "")).startswith(pinned): return True, f"CI build {m.get('yabridge_commit', '?')} for wine {pinned}"
        return False, f"built for wine {m.get('wine_version', '?')}, but this app pins wine {pinned}"
    if not needs_master(pinned): return True, f"upstream release; fine with wine {pinned}"
    return False, (f"upstream release {installed()} predates Wine 9.22's window changes: with wine {pinned} mouse clicks in "
                   "bridged plugin GUIs land in the wrong place, in every DAW")

def _find_build_tarball(wine_version: str, r) -> tuple[Path | None, str]:
    """A CI-built yabridge for this Wine: VSTENV_YABRIDGE_TARBALL, else the asset
    attached to the latest release of RELEASE_REPOS. Returns (local path, label)."""
    override = os.environ.get("VSTENV_YABRIDGE_TARBALL")
    if override:
        f = Path(override).expanduser()
        if f.is_file(): return f, f"{f.name} (VSTENV_YABRIDGE_TARBALL)"
        r.log(f"VSTENV_YABRIDGE_TARBALL={override} does not exist; ignoring")
    for repo in RELEASE_REPOS:
        try:
            rel = json.loads(text(f"https://api.github.com/repos/{repo}/releases/latest"))
        except Exception as e:
            r.log(f"could not read {repo} releases: {str(e)[:80]}"); continue
        a = pick_asset(rel.get("assets", []), wine_version)
        if a: return fetch(a["browser_download_url"], paths.DOWNLOADS / a["name"], reporter=r, label="yabridge"), f"{a['name']} ({repo} release {rel.get('tag_name', '')})"
    return None, ""

def _install_tarball(tgz: Path, r):
    """Put the tarball's yabridge/ contents into ~/.local/share/yabridge.

    The directory itself is never renamed or removed: under Flatpak it is a
    `--filesystem=` grant, i.e. a bind mount, and renaming a mount point fails
    with EBUSY. Files are replaced one by one through a temporary name in the
    same directory, which is also what keeps a running DAW safe -- a plugin has
    the old libraries mapped, and overwriting them in place would crash it,
    while a rename just leaves it on the old inode.
    """
    YAB_DIR.mkdir(parents=True, exist_ok=True)
    staging = YAB_DIR.parent / (YAB_DIR.name + ".new")
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    try:
        with tarfile.open(tgz) as t: t.extractall(staging, filter="tar")
        src = staging / "yabridge"
        if not src.is_dir():                      # tarball without the leading directory
            src = staging
        bak = YAB_DIR / "previous"                # keep the replaced files inside the mount
        shutil.rmtree(bak, ignore_errors=True); bak.mkdir(parents=True, exist_ok=True)
        for f in sorted(src.iterdir()):
            if f.is_dir(): continue               # the release tarballs are flat
            dst = YAB_DIR / f.name
            if dst.exists():
                try: shutil.copy2(dst, bak / f.name)
                except OSError: pass
            tmp = YAB_DIR / f".{f.name}.new"
            shutil.copy2(f, tmp)
            tmp.replace(dst)                      # atomic within the same directory
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    (Path.home() / ".local/bin").mkdir(parents=True, exist_ok=True)
    link = Path.home() / ".local/bin/yabridgectl"
    if not link.exists(): link.symlink_to(YCTL)

def install(reporter=None, force=False) -> str:
    """Install yabridge, or replace an upstream release that cannot work with the
    pinned Wine by our CI build of yabridge master; then make sure its
    host launcher runs each prefix with the wine it records. Idempotent."""
    r = null_reporter(reporter)
    res = _install(r, force)
    ensure_host_launchers(r)
    return res

def _install(r, force) -> str:
    pinned = pinned_wine_version()
    marker = build_marker()
    if YCTL.exists() and not force:
        if marker and pinned and str(marker.get("wine_version", "")).startswith(pinned): return installed() or "present"
        if not marker and not needs_master(pinned): return installed() or "present"
    r.step("Installing yabridge" + (f" for wine {pinned}" if pinned else ""))
    tgz, label = _find_build_tarball(pinned, r)
    if tgz is not None:
        _install_tarball(tgz, r)
        if not MARKER.exists():
            MARKER.write_text(json.dumps({"yabridge_ref": "unknown", "yabridge_commit": "unknown", "wine_version": pinned}))
        r.ok(label); return installed() or "present"
    if YCTL.exists():
        r.skip(f"keeping {installed()}; no CI build for wine {pinned} is published yet"); return installed() or "present"
    rel = json.loads(text("https://api.github.com/repos/robbert-vdh/yabridge/releases/latest"))
    url = next(a["browser_download_url"] for a in rel["assets"]
               if re.fullmatch(r"yabridge-[0-9.]+\.tar\.gz", a["name"]))
    tgz = fetch(url, paths.DOWNLOADS / Path(url).name, reporter=r, label="yabridge")
    _install_tarball(tgz, r)
    r.ok(rel["tag_name"] + (" -- NOTE: too old for this wine, see Health" if needs_master(pinned) else "")); return rel["tag_name"]

def registry_vst_path(p: Prefix) -> Path | None:
    for hive in ("system.reg", "user.reg"):
        f = p.path / hive
        if not f.exists(): continue
        txt = f.read_text(errors="ignore")
        for m in re.finditer(r"^\[Software\\\\(?:Wow6432Node\\\\)?VST\][^\[]*", txt, re.M | re.S):
            v = re.search(r'"VSTPluginsPath"="(.*)"', m.group(0))
            if v:
                host = p.to_host(v.group(1).replace("\\\\", "\\"))
                if host.is_dir(): return host
    return None

def plugin_dirs(p: Prefix, extras=()) -> list[Path]:
    """Standard dirs (created), registry VST2 path, discovered .vst3/.clap dirs, extras."""
    out: list[Path] = []
    def add(d: Path):
        d = Path(d)
        try: rel = str(d.resolve().relative_to(p.drive_c.resolve()))
        except ValueError: rel = None
        if rel is not None and rel.rstrip("/") in BROAD or rel == ".": return
        if d not in out: out.append(d)
    from . import vendors
    for rel in STANDARD_DIRS + [d for v in vendors.all() for d in v.plugin_dirs()]:
        d = p.drive_c / rel; d.mkdir(parents=True, exist_ok=True); add(d)
    rp = registry_vst_path(p)
    if rp: add(rp)
    pf = p.drive_c / "Program Files"
    for f in sorted(pf.rglob("*")):
        if f.suffix.lower() in (".vst3", ".clap") and "Program Files (x86)" not in str(f):
            if len(f.relative_to(pf).parts) <= 6: add(f.parent)
    for e in extras:
        d = Path(e) if str(e).startswith("/") else p.drive_c / e
        if not d.is_dir(): raise FileNotFoundError(f"extra plugin dir not found: {d}")
        add(d)
    return out

def plugin_signature(p: Prefix) -> frozenset:
    """What plugins exist in the prefix right now, cheap enough to poll.

    Name and size only -- opening or hashing 175MB of Kontakt every few seconds
    to answer "has anything appeared?" would be absurd. A plugin being replaced
    by a different build of the same size is not something this needs to catch;
    the exit-time sync covers that.
    """
    out = set()
    pf = p.drive_c / "Program Files"
    if not pf.is_dir():
        return frozenset()
    for f in pf.rglob("*"):
        if f.suffix.lower() not in (".vst3", ".clap", ".dll"):
            continue
        if "Program Files (x86)" in str(f):
            continue
        try:
            if len(f.relative_to(pf).parts) > 6:
                continue
            out.add((str(f), f.stat().st_size))
        except OSError:
            continue        # vanished or unreadable mid-scan: it will show up next time
    return frozenset(out)

def sync(p: Prefix, reporter=None, extras=()) -> dict:
    r = null_reporter(reporter)
    install(r)
    # Never hand plugins to a DAW that would run them with another Wine: its
    # prefix update replaces this prefix's DLLs (seen with the host's wine 9.0).
    p.declare_wine(r)
    st, detail = plugin_wine_status(p)
    if st != "active":
        r.step("Bridging plugins"); r.skip(f"not yet — {detail}")
        return {"dirs": [], "returncode": 0, "output": "", "summary": f"plugins not bridged: {detail}", "skipped": st}
    dirs = plugin_dirs(p, extras)
    r.step("Registering plugin directories")
    env = _yctl_env(p)
    for d in dirs:
        subprocess.run([str(YCTL), "add", str(d)], capture_output=True, env=env)
    r.ok(f"{len(dirs)} directories")
    # The vstenv that runs owns the bridges. Directories of *other* vstenv prefixes
    # (the Flatpak vs a source install) would make same-named plugins link into a
    # prefix whose vendor daemon is not the one running -- and hang in every DAW.
    # Third-party prefixes the user registered themselves are left alone.
    from . import prefixes
    foreign = prefixes.foreign_yabridge_dirs(p, status(p))
    removed = []
    if foreign:
        r.step("Unregistering other vstenv prefixes' plugin directories")
        for d in foreign:
            cp0 = subprocess.run([str(YCTL), "rm", d], capture_output=True, text=True, env=env)
            if cp0.returncode == 0: removed.append(d)
            else: r.log(f"could not remove {d}: {(cp0.stderr or cp0.stdout).strip()[:100]}")
        owners = sorted({prefixes.short(prefixes.prefix_of_dir(d)) for d in removed if prefixes.prefix_of_dir(d)})
        r.ok(f"{len(removed)} from {', '.join(owners)}" if removed else "nothing removed")
    r.step("Syncing yabridge")
    cp = subprocess.run([str(YCTL), "sync", "--prune"], capture_output=True, text=True, env=env, timeout=1800)
    summary = next((l for l in cp.stdout.splitlines() if l.startswith("Finished")), "")
    if not summary: summary = (cp.stderr or cp.stdout).strip().splitlines()[-1:] or [""]; summary = summary[0][:160]
    (r.ok if cp.returncode == 0 else r.fail)(summary)
    for l in cp.stdout.splitlines():
        if l.startswith("WARNING"): r.log(l)
    return {"dirs": [str(d) for d in dirs], "removed_dirs": removed, "returncode": cp.returncode, "output": cp.stdout, "summary": summary}

def status(p: Prefix) -> str:
    if not YCTL.exists(): return "yabridge not installed"
    return subprocess.run([str(YCTL), "status"], capture_output=True, text=True, env=_yctl_env(p), timeout=120).stdout

# --- which wine runs the plugin host ---------------------------------------------------------------
# yabridge starts its Windows-side plugin host through yabridge-host.exe, a
# winegcc launcher script that runs `$WINELOADER` or else the `wine` on PATH.
# By then libyabridge has set WINEPREFIX to the prefix it detected from the
# plugin's location. The app installs that script, so it teaches it one thing
# more: a prefix that records its wine in <prefix>/wineloader is run with that
# wine (Prefix.declare_wine writes the record). Any other prefix, and an
# explicit WINELOADER, behave exactly as upstream. Nothing goes on PATH and
# nothing is set in the session, so this holds for every DAW however it is
# started, and a wine the user already has (Proton, a distro package, their own
# ~/.local/bin/wine) is never touched.
HOST_LAUNCHERS = ("yabridge-host.exe", "yabridge-host-32.exe")
LAUNCHER_MARK = "# wineloader: a prefix that records its wine in <prefix>/wineloader is run with that wine"
# nilinux patches the same launcher with the same block; either mark means "done"
KNOWN_MARKS = (LAUNCHER_MARK, "# nilinux: a prefix that records its wine in <prefix>/wineloader is run with that wine")
_UPSTREAM_LOADER = 'if [ ! -x "$WINELOADER" ]; then WINELOADER="wine"; fi\n'
_OUR_LOADER = f'''{LAUNCHER_MARK}
if [ ! -x "$WINELOADER" ] && [ -n "$WINEPREFIX" ] && [ -r "$WINEPREFIX/{Prefix.WINELOADER_FILE}" ]; then
    IFS= read -r WINELOADER < "$WINEPREFIX/{Prefix.WINELOADER_FILE}"
    [ -x "$WINELOADER" ] && export WINEFSYNC=1    # the sync mode of every other client of this prefix's wineserver
fi
{_UPSTREAM_LOADER}'''

def host_launchers() -> list[Path]:
    return [YAB_DIR / n for n in HOST_LAUNCHERS if (YAB_DIR / n).exists()]

def patch_host_launcher(script: Path) -> str:
    """'patched' | 'already' | 'unrecognised'. Same-directory atomic replace, mode kept."""
    txt = script.read_text(errors="replace")
    if any(m in txt for m in KNOWN_MARKS): return "already"
    if txt.count(_UPSTREAM_LOADER) != 1: return "unrecognised"
    tmp = script.with_name(f".{script.name}.new")
    tmp.write_text(txt.replace(_UPSTREAM_LOADER, _OUR_LOADER, 1)); tmp.chmod(script.stat().st_mode)
    tmp.replace(script)
    return "patched"

def ensure_host_launchers(reporter=None) -> bool:
    """Patch every installed host launcher. True when all of them honour the record."""
    r = null_reporter(reporter)
    r.step("Plugin hosts run each prefix with the wine it records")
    ls = host_launchers()
    if not ls: r.fail(f"no yabridge-host.exe in {YAB_DIR}"); return False
    res = {l.name: patch_host_launcher(l) for l in ls}
    bad = [n for n, st in res.items() if st == "unrecognised"]
    if bad: r.fail(f"{', '.join(bad)}: not the winegcc launcher this app knows -- plugins would run with the host's wine"); return False
    (r.ok if "patched" in res.values() else r.skip)(", ".join(f"{n} {st}" for n, st in res.items()))
    return True

def plugin_wine_status(p: Prefix) -> tuple[str, str]:
    """('active' | 'missing', detail). active: a DAW's plugin host runs this prefix
    with this wine (the prefix records it and the launcher honours the record).
    missing: it would use the host's wine, whose prefix update rewrites the
    app's prefix with another Wine's DLLs."""
    try: rec = p.wineloader_file.read_text(errors="replace").strip()
    except OSError: rec = ""
    if rec != str(p.build.wine): return "missing", f"{p.wineloader_file} does not record this wine"
    ls = host_launchers()
    if not ls: return "missing", "yabridge is not installed"
    plain = [l.name for l in ls if not any(m in l.read_text(errors="replace") for m in KNOWN_MARKS)]
    if plain: return "missing", f"{', '.join(plain)} would run plugins with the host's wine (an upstream yabridge installed over ours?)"
    return "active", f"yabridge's host launcher reads {p.wineloader_file.name} and runs {p.build.root.name}; other prefixes keep their own wine"

def broken_bundles() -> list[Path]:
    """yabridge VST3 bundles whose Windows plugin link no longer resolves (the
    plugin was uninstalled, or an update removed it and did not finish)."""
    out = []
    root = Path.home() / ".vst3/yabridge"
    if not root.is_dir(): return out
    for bundle in sorted(root.glob("*.vst3")):
        win = bundle / "Contents/x86_64-win"
        links = list(win.iterdir()) if win.is_dir() else []
        if links and any(l.is_symlink() and not l.exists() for l in links): out.append(bundle)
    return out

def bridged(p: Prefix) -> list[dict]:
    """Parsed `yabridgectl status`, limited to plugin dirs inside this prefix
    (yabridge's config is global and may list other prefixes), deduplicated by plugin name."""
    out, seen, cur_in_prefix = [], set(), False
    root = str(p.drive_c.resolve())
    for line in status(p).splitlines():
        if not line.startswith(" ") and line.rstrip().endswith("/"):
            cur_in_prefix = str(Path(line.strip()).resolve()).startswith(root); continue
        if cur_in_prefix and "::" in line:
            name, info = (x.strip() for x in line.split("::", 1))
            if name in seen: continue
            seen.add(name); out.append({"name": name, "info": info})
    return out
