"""Health checks: the environment's own, then every vendor module's."""
import os, shutil
from pathlib import Path
from . import APP_ID, paths, wine, runtime, yabridge, prefixes, dxvk, host, menu, vendors, winefixes, dcomp, desktopfix, webview2, wedge
from .vendors import Check

class _Checks(list):
    """A list that reports each Check to a callback as it is appended."""
    def __init__(self, on_check=None):
        super().__init__(); self._on = on_check
    def append(self, item):
        super().append(item)
        if self._on is not None:
            try: self._on(item)
            except Exception: pass

def run(p: wine.Prefix | None = None, on_check=None) -> list[Check]:
    """Every health check. Takes a while: several start Wine or probe ports.
    `on_check` is called with each Check as it completes, so a UI can show
    results as they arrive instead of a blank page until the last one lands."""
    c = _Checks(on_check)
    from . import tools
    sz = tools.seven_zip_status()
    c.append(Check("7-Zip (24+) for unpacking vendor packages", bool(sz["path"]), f"{sz['version']} ({sz['source']})" if sz["path"] else "none new enough on this host; setup fetches the official static build",
                   fix="vstenv setup"))
    try: import olefile; c.append(Check("python: olefile", True))
    except ImportError: c.append(Check("python: olefile", False, fix="pip install olefile"))
    b = wine.installed_build()
    c.append(Check("wine build", b is not None, b.version() if b else "not provisioned", fix="vstenv setup"))
    if p is None:
        if b is None: return c
        p = wine.Prefix(paths.PREFIX, b)
    wf = winefixes.status(b)
    c.append(Check("patched Wine DLLs (DirectComposition, vblank wait, credential attributes)", wf["installed"],
                   ", ".join(wf["files"]) if wf["installed"] else f"{', '.join(wf['missing'])} not installed: DirectComposition GUIs (Steinberg, JUCE 8) are refused, hang or stay blank, or Steinberg's sign-in is forgotten",
                   fix="vstenv wine-fixes install (fetches this app's build from its release)"))
    ok, detail = wine.module_libs_check(wine.missing_module_libs(b))
    c.append(Check("Wine modules find their host libraries", ok, detail, fix="install the named libraries with your package manager (optional modules only lose that feature)"))
    c.append(Check("prefix", p.exists, str(p.path), fix="vstenv setup"))
    if not p.exists: return c
    try:
        exes = dcomp.program_exes(p); missing = [e for e in exes if not dcomp.program_fix_applied(p, e)]
        c.append(Check("DirectComposition programs run on Wine's own Direct3D", not missing,
                       (", ".join(exes) if exes else "none installed") if not missing else f"{', '.join(missing)}: DXVK has no composition swap chains, so their windows stay blank",
                       fix="vstenv setup"))
    except Exception as e: c.append(Check("DirectComposition programs run on Wine's own Direct3D", False, str(e)[:100], fix="vstenv setup"))
    hok, hdetail = host.available()
    c.append(Check("Wine runs on the host", hok, hdetail, fix="reinstall the current Flatpak build (it grants the org.freedesktop.Flatpak portal)"))
    if not hok: return c
    try:
        stuck = wedge.detect(p)
        c.append(Check("DirectComposition programs are not wedged", not stuck,
                       "none hung" if not stuck else
                       f"{len(stuck)} program(s) hung windowless after loading dcomp.dll (pids "
                       f"{', '.join(str(s) for s in stuck)}); every DirectComposition GUI stays hung until "
                       "the Wine session restarts",
                       fix="vstenv wedge clear (closes running Windows programs, including a DAW's plugins)"))
    except Exception as e:
        c.append(Check("DirectComposition programs are not wedged", True, f"not checked: {str(e)[:80]}"))
    scope = p.wineserver_scope()
    c.append(Check("wineserver reachable from here", scope != "foreign",
                   {"none": "not running (starts on first use)", "ours": "running on the host, reachable from here",
                    "foreign": "running in a pid namespace this app cannot reach (a sandboxed DAW with its own Wine?)",
                    "unknown": "running; could not read /proc/locks or scan /proc just now (retry)"}[scope],
                   fix="close the DAW or the other instance and wait for its wineserver to exit"))
    rs = runtime.status(p)
    c.append(Check("prefix prepared (fonts, C runtime)", rs["prepared"], fix="vstenv setup"))
    if rs["prepared"]:
        c.append(Check("real ucrtbase.dll", rs["ucrtbase"], fix="vstenv setup"))
        c.append(Check("VC++ 2022 runtime", rs["vc_runtime"], fix="vstenv setup"))
        c.append(Check("Segoe UI font family for DirectWrite", rs["segoe"], "" if rs["segoe"] else "programs drawing dialog text with DirectWrite (Steinberg's) show blank dialogs", fix="vstenv setup"))
    foreign = p.foreign_dlls()
    c.append(Check("prefix files from this wine", not foreign,
                   f"{', '.join(foreign)} were written by another Wine (a DAW using the host wine?)" if foreign else "", fix="vstenv setup (refreshes them)"))
    # Inside a Flatpak without --allow=multiarch every 32-bit program fails at once
    cp = p.run([r"C:\windows\syswow64\cmd.exe", "/c", "echo ok"], timeout=120)
    c.append(Check("32-bit programs run (WoW64)", cp.returncode == 0 and "ok" in cp.stdout,
                   "" if cp.returncode == 0 else "cannot start a 32-bit program: 32-bit installers will fail (Flatpak: needs --allow=multiarch)",
                   fix="reinstall the current Flatpak build"))
    others = prefixes.other_prefixes(p)
    c.append(Check("single vstenv prefix", not others,
                   "" if not others else "also: " + ", ".join(prefixes.short(o) for o in others) + " -- sync keeps the bridges on this one",
                   fix="delete the other prefix folders once you are sure this one is the install to keep"))
    if prefixes.flatpak_filesystems() is not None:
        gaps = prefixes.sandbox_gaps(p)
        c.append(Check("sandbox sees every path the prefix links to", not gaps,
                       "" if not gaps else "invisible from this sandbox: " + ", ".join(gaps)
                       + " -- when this app starts the prefix's wineserver, host DAW plugins cannot open them",
                       fix=f"flatpak override --user --filesystem=host {APP_ID} (the current manifest grants it; reinstall the app)"))
    d = dxvk.status(p)
    if not d["vulkan_ok"]:
        c.append(Check("DXVK (plugin GUI rendering)", True,
                       f"not used: {d['vulkan']}; Wine's own renderer is fine for most plugins, but some (JUCE/Direct3D GUIs) will not repaint until their window is resized",
                       fix="install a Vulkan driver for your GPU, then: vstenv dxvk install"))
    else:
        c.append(Check("DXVK (plugin GUI rendering)", d["installed"],
                       (f"{d['version']} on {d['vulkan']}" if d["installed"] else f"Vulkan is available ({d['vulkan']}) but DXVK is not installed"), fix="vstenv dxvk install"))
    ystat = yabridge.status(p)
    foreign_dirs = prefixes.foreign_yabridge_dirs(p, ystat)
    c.append(Check("yabridge lists only this prefix", not foreign_dirs,
                   "" if not foreign_dirs else f"{len(foreign_dirs)} plugin directories of other vstenv prefixes are registered", fix="vstenv sync"))
    in_use = yabridge.plugins_in_use(p)
    c.append(Check("no plugin loaded by a DAW right now", not in_use,
                   "" if not in_use else "loaded: " + ", ".join(in_use) + " -- vendor installers and updates of these fail with 'files in use' until the DAW unloads them",
                   fix="close the DAW (or remove the plugin from its session) before installing or updating it"))
    broken = yabridge.broken_bundles()
    c.append(Check("bridged plugins point at existing files", not broken,
                   "" if not broken else "missing target for: " + ", ".join(b.name for b in broken) + " (uninstalled, or an update that did not finish)",
                   fix="vstenv finish-installs, then vstenv sync"))
    # Wine's audio driver speaks the PulseAudio protocol; PipeWire serves that
    # socket too. Without it, standalone programs are silent (DAW use is unaffected).
    pulse = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")) / "pulse" / "native"
    c.append(Check("audio server (PulseAudio/PipeWire socket)", pulse.exists(),
                   str(pulse) if pulse.exists() else "no Pulse/PipeWire socket: standalone programs will have no sound (plugins in a DAW are unaffected)",
                   fix="install and start pipewire-pulse (or pulseaudio)"))
    yv = yabridge.installed()
    c.append(Check("yabridge", yv is not None, yv or "", fix="vstenv sync"))
    if yv is not None:
        yok, ydetail = yabridge.compatibility()
        c.append(Check("yabridge matches this wine", yok, ydetail, fix="vstenv sync (installs the build for this wine)"))
    left = yabridge.nilinux_leftovers()
    c.append(Check("no machine-wide Wine redirection left by nilinux", not left,
                   "" if not left else ", ".join(str(f) for f in left) + " sends every Wine process on this machine, including plugins in your other prefixes, to one Wine",
                   fix="vstenv setup (removes it)"))
    st, detail = yabridge.plugin_wine_status(p)
    c.append(Check("plugin hosts run this prefix with this wine", st == "active", detail, fix="vstenv setup"))
    entries = menu.ours()
    # The desktop drops an entry whose Exec program it cannot find, without a word.
    broken = [f.name for f in entries if not menu.exec_resolves(f)]
    c.append(Check("desktop menu entries", not broken,
                   f"{len(entries)} program{'s' if len(entries) != 1 else ''} in the app menu" if not broken
                   else f"{len(broken)} of {len(entries)} entries name a launcher the desktop cannot find (hidden from the menu)",
                   fix="vstenv menu update"))
    ws = webview2.status(p)
    if ws["apps"]:
        c.append(Check("WebView2 runtime for embedded-Edge apps", ws["installed"],
                       (f"present; apps using it: {', '.join(ws['apps'])} (their UI may still not render or take keys under Wine)" if ws["installed"]
                        else f"missing; {', '.join(ws['apps'])} will crash at startup — install it"),
                       fix="vstenv webview2  (or Re-run setup / repair)"))
    ds = desktopfix.status()
    if ds["applies"]:
        c.append(Check("Wine windows do not jump to the front (Cinnamon)", ds["ok"],
                       f"focus-new-windows = {ds['value']}" if ds["ok"]
                       else f"focus-new-windows = {ds['value']}: Wine plugin windows raise themselves on every click; strict stops it",
                       fix="vstenv setup"))
    for v in vendors.all():
        try:
            for ck in v.checks(p): c.append(ck)
        except Exception as e:
            c.append(Check(f"{v.name} checks", False, f"failed: {str(e)[:80]}"))
    return c
