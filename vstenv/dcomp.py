"""Programs and plugins that draw through DirectComposition.

Steinberg's VSTGUI, JUCE 8 (its Direct2D window renderer, the default on
Windows since 8.0: Spitfire Audio's app, and every plugin built with it) and
Qt's D3D backend all import dcomp.dll. Under Wine they need three things this
app arranges: the patched dcomp.dll from the wine-fixes channel (winefixes.py),
Wine's own Direct3D 11 instead of DXVK for that one program (DXVK has no
composition swap chains, and the compositor blits between them with wined3d),
and a dxgi.dll whose WaitForVBlank returns instead of E_NOTIMPL (JUCE's vblank
thread never exits otherwise, and its main thread waits for it forever).

This module finds them by their import tables: a program whose exe, or a DLL
next to it, imports dcomp.dll gets a per-exe DllOverrides key; a bridged
plugin whose PE imports it gets a line in the launcher's plugin-dll-overrides
file (yabridge.py). Vendor modules add what they know on top (Steinberg names
its programs outright).
"""
import json
from pathlib import Path
from . import paths, pe
from .progress import null_reporter
from .wine import Prefix

BUILTIN = ("d3d10core", "d3d11", "dxgi")
OVERRIDE_VALUE = ",".join(BUILTIN) + "=b"
CACHE = paths.DATA / "dcomp-imports.json"      # path -> [size, mtime, imports dcomp]

NOTE_TEXT = ("Draws through DirectComposition: JUCE 8 made Direct2D and DirectComposition its default Windows renderer with no "
             "fallback, so every program built on it inherits Steinberg's problem, and its vblank thread ignores its exit flag when "
             "the wait fails, so under stock Wine it hangs before a window appears. It runs here on this app's patched dcomp.dll and "
             "dxgi.dll, with Wine's own Direct3D for this program.")

def note():
    from .vendors import Note
    return Note("", "patched", NOTE_TEXT)

def is_dcomp_program(prog, exe_names) -> bool:
    return bool(prog.exe) and prog.exe.rsplit("\\", 1)[-1] in exe_names

def overrides_key(exe_name: str) -> str: return rf"HKCU\Software\Wine\AppDefaults\{exe_name}\DllOverrides"

def program_fix_applied(p: Prefix, exe_name: str) -> bool:
    cur = p.reg_query(overrides_key(exe_name))
    return all(cur.get(d) == "builtin" for d in BUILTIN)

def apply_program_overrides(p: Prefix, exe_names: list[str], reporter=None,
                            why="their GUIs draw through DirectComposition") -> list[str]:
    """Wine's own Direct3D 11 for each exe. Returns what changed."""
    r = null_reporter(reporter); changed = []
    for name in sorted(set(exe_names)):
        if program_fix_applied(p, name): continue
        for d in BUILTIN: p.reg_add(overrides_key(name), d, "builtin")
        changed.append(name)
    if changed:
        r.step(f"Wine's own Direct3D 11 for programs ({why})"); r.ok(", ".join(changed))
    return changed

# -- import scan, cached by size and mtime (an exe can be 50 MB) --------------------------
def _load() -> dict:
    try: return json.loads(CACHE.read_text())
    except (OSError, ValueError): return {}

def _save(c: dict):
    try: CACHE.parent.mkdir(parents=True, exist_ok=True); CACHE.write_text(json.dumps(c))
    except OSError: pass

def imports_dcomp(f: Path, cache: dict | None = None) -> bool:
    try: st = f.stat()
    except OSError: return False
    key = str(f); own = cache is None
    if own: cache = _load()
    ent = cache.get(key)
    if ent and ent[0] == st.st_size and ent[1] == int(st.st_mtime): return ent[2]
    res = "dcomp.dll" in pe.imports(f)
    cache[key] = [st.st_size, int(st.st_mtime), res]
    if own: _save(cache)
    return res

def program_exes(p: Prefix) -> list[str]:
    """Exe names of installed programs that draw through DirectComposition: the exe
    itself, or a DLL beside it, imports dcomp.dll."""
    from . import programs
    cache = _load(); out = set()
    for prog in programs.installed(p):
        if not prog.exe or not prog.install_dir: continue
        try: d = p.to_host(prog.install_dir)
        except Exception: continue
        if not d.is_dir(): continue
        exe = prog.exe.rsplit("\\", 1)[-1]
        files = [f for f in d.iterdir() if f.is_file() and f.suffix.lower() in (".exe", ".dll")]
        if any(imports_dcomp(f, cache) for f in files): out.add(exe)
    _save(cache)
    return sorted(out)

def plugin_overrides(p: Prefix) -> list[tuple[str, str]]:
    """(bundle path, overrides) for every bridged plugin whose PE imports dcomp.dll.
    The path is what yabridge passes its host, so the launcher matches on it."""
    from . import yabridge
    cache = _load(); out = []
    for d in yabridge.plugin_dirs(p):
        for f in sorted(d.rglob("*")):
            if not f.is_file() or f.suffix.lower() not in (".vst3", ".clap", ".dll") or "Program Files (x86)" in str(f): continue
            if not imports_dcomp(f, cache): continue
            bundle = next((a for a in (f, *f.parents) if a.suffix.lower() == ".vst3" and a.is_dir()), f)
            entry = (str(bundle), OVERRIDE_VALUE)
            if entry not in out: out.append(entry)
    _save(cache)
    return out

def all_plugin_overrides(p: Prefix) -> list[tuple[str, str]]:
    """Vendor-declared entries, then the scanned ones."""
    from . import vendors
    out = [e for v in vendors.all() for e in v.plugin_dll_overrides(p)]
    return out + [e for e in plugin_overrides(p) if e not in out]
