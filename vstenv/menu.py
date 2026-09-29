"""Desktop menu entries for the Windows programs in the prefix.

Wine's own menu builder is disabled here (installers would spam the menu with
uninstallers, readmes and helper tools). Instead, every runnable program the
prefix knows a launcher for gets one curated entry, started through `vstenv run`
so the vendor's launch fixes and quirks apply exactly as from the Programs page.
Vendor managers and installers are excluded: they have to run through the app so
their fixes apply, and a raw shortcut would start them without those.
Icons are pulled from the exe's own resources. Entries are ours by file name;
stale ones (the program is gone) are removed on every sync.
"""
from __future__ import annotations

import re, sys
from pathlib import Path

from . import APP_ID, APP_NAME, paths, host, pe
from .progress import null_reporter
from .wine import Prefix

APPS = Path.home() / ".local/share/applications"
ICONS = paths.DATA / "icons"
PREFIX = f"{APP_ID}.program."

def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "program"

def desktop_path(name: str) -> Path: return APPS / f"{PREFIX}{slug(name)}.desktop"

def _q(s: str) -> str:
    """Quote one Exec= argument the way the desktop entry spec wants."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'

def launcher() -> str:
    """The command the desktop can run to reach this app. The desktop loads an
    entry only if its Exec program exists on PATH (GDesktopAppInfo drops it
    otherwise, silently), so a source checkout without the `vstenv` script must
    point at its interpreter instead."""
    if host.in_flatpak(): return f"flatpak run --command={APP_NAME} {APP_ID}"
    if host.which(APP_NAME): return APP_NAME
    return f"{_q(sys.executable)} -m {APP_NAME}"

def exec_line(name: str) -> str:
    """How the desktop starts the program: through this app, so quirks and vendor launchers apply."""
    return f"{launcher()} run {_q(name)}"

def _icon_for(p: Prefix, prog) -> str:
    """Extract the exe's icon once; fall back to the app icon."""
    try:
        exe = p.to_host(prog.exe)
        dst = ICONS / f"{slug(prog.name)}.ico"
        if dst.exists() and exe.exists() and dst.stat().st_mtime >= exe.stat().st_mtime: return str(dst)
        ico = pe.icon(exe)
        if ico:
            ICONS.mkdir(parents=True, exist_ok=True); dst.write_bytes(ico); return str(dst)
    except OSError: pass
    return APP_ID

def entry(p: Prefix, prog) -> str:
    from . import vendors
    v = vendors.for_program(prog)
    by = f" · {v.name}" if v else (f" · {prog.publisher}" if prog.publisher else "")
    return (f"[Desktop Entry]\nType=Application\nName={prog.name}\n"
            f"Comment=Windows program in the {APP_NAME} environment{by}\n"
            f"Exec={exec_line(prog.name)}\nIcon={_icon_for(p, prog)}\nTerminal=false\n"
            f"Categories=AudioVideo;Audio;\nKeywords=VST;{APP_NAME};\nStartupNotify=false\n"
            f"X-{APP_NAME}-program={prog.name}\n")

def ours() -> list[Path]:
    return sorted(APPS.glob(f"{PREFIX}*.desktop")) if APPS.is_dir() else []

def exec_resolves(desktop: Path) -> bool:
    """Would the desktop accept this entry? Its Exec program must be an absolute
    path that exists or a name found on PATH."""
    try:
        line = next((l for l in desktop.read_text(errors="replace").splitlines() if l.startswith("Exec=")), "")
    except OSError: return False
    m = re.match(r'Exec=(?:"([^"]+)"|(\S+))', line)
    if not m: return False
    prog = m.group(1) or m.group(2)
    return Path(prog).is_file() if "/" in prog else host.which(prog) is not None

def sync(p: Prefix, reporter=None) -> dict:
    """Write an entry per runnable program, remove entries for programs that are gone."""
    from . import programs, vendors
    r = null_reporter(reporter)
    r.step("Desktop menu entries for the prefix's programs")
    # No shortcut for a vendor manager/installer: it has to run through the app so
    # its launch fixes apply (a raw shortcut would start it without them).
    progs = [x for x in programs.installed(p) if x.runnable and not vendors.run_through_app(x)]
    want = {desktop_path(x.name): x for x in progs}
    written = removed = 0
    try:
        APPS.mkdir(parents=True, exist_ok=True)
        for f, prog in want.items():
            body = entry(p, prog)
            if not f.exists() or f.read_text(errors="replace") != body: f.write_text(body); written += 1
        for f in ours():
            if f not in want: f.unlink(); removed += 1
        if written or removed: host.desktop_tool(["update-desktop-database", str(APPS)])
    except OSError as e:
        r.fail(str(e)[:100]); return {"programs": len(progs), "written": written, "removed": removed}
    r.ok(f"{len(progs)} program{'s' if len(progs) != 1 else ''}" + (f", {written} updated" if written else "") + (f", {removed} removed" if removed else ""))
    return {"programs": len(progs), "written": written, "removed": removed}

def remove_all(reporter=None) -> int:
    r = null_reporter(reporter)
    r.step("Removing the menu entries")
    n = 0
    for f in ours():
        try: f.unlink(); n += 1
        except OSError: pass
    if n: host.desktop_tool(["update-desktop-database", str(APPS)])
    r.ok(f"{n} removed" if n else "none")
    return n
