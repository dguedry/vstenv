"""Health checks: the environment's own, then every vendor module's."""
import os, shutil
from pathlib import Path
from . import APP_ID, paths, wine, runtime, yabridge, prefixes, dxvk, host, menu, vendors
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
    for tool in ("7z", "cabextract"):
        c.append(Check(f"host tool: {tool}", bool(shutil.which(tool)), fix=f"install {tool} with your package manager"))
    try: import olefile; c.append(Check("python: olefile", True))
    except ImportError: c.append(Check("python: olefile", False, fix="pip install olefile"))
    b = wine.installed_build()
    c.append(Check("wine build", b is not None, b.version() if b else "not provisioned", fix="vstenv setup"))
    if p is None:
        if b is None: return c
        p = wine.Prefix(paths.PREFIX, b)
    c.append(Check("prefix", p.exists, str(p.path), fix="vstenv setup"))
    if not p.exists: return c
    hok, hdetail = host.available()
    c.append(Check("Wine runs on the host", hok, hdetail, fix="reinstall the current Flatpak build (it grants the org.freedesktop.Flatpak portal)"))
    if not hok: return c
    scope = p.wineserver_scope()
    c.append(Check("wineserver reachable from here", scope != "foreign",
                   {"none": "not running (starts on first use)", "ours": "running on the host, reachable from here",
                    "foreign": "running in a pid namespace this app cannot reach (a sandboxed DAW with its own Wine?)"}[scope],
                   fix="close the DAW or the other instance and wait for its wineserver to exit"))
    rs = runtime.status(p)
    c.append(Check("prefix prepared (fonts, C runtime)", rs["prepared"], fix="vstenv setup"))
    if rs["prepared"]:
        c.append(Check("real ucrtbase.dll", rs["ucrtbase"], fix="vstenv setup"))
        c.append(Check("VC++ 2022 runtime", rs["vc_runtime"], fix="vstenv setup"))
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
    st, detail = yabridge.plugin_wine_status(p)
    c.append(Check("plugin hosts run this prefix with this wine", st == "active", detail, fix="vstenv setup"))
    entries = menu.ours()
    c.append(Check("desktop menu entries", True, f"{len(entries)} program{'s' if len(entries) != 1 else ''} in the app menu", fix="vstenv menu update"))
    for v in vendors.all():
        try:
            for ck in v.checks(p): c.append(ck)
        except Exception as e:
            c.append(Check(f"{v.name} checks", False, f"failed: {str(e)[:80]}"))
    return c
