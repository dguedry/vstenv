"""Keep the window manager away from programs whose menus are fullscreen popups.

Some programs do not use real Win32 menus for their dropdowns: they open a
fullscreen, transparent popup window and draw the menu inside it. Wine hands
such a window to the window manager -- is_window_managed() in winex11 returns
TRUE for a fullscreen WS_POPUP -- and a window manager treats a fullscreen
dialog as something to activate. Activating it deactivates the program's main
window, the program sees its own menu lose activation, and it closes the menu
again. The menu appears for a few frames and vanishes.

Komplete Kontrol 2.9.6 does exactly this: every dropdown (the browser's content
selector, the plug-in "+" slot, even the File menu) flashed and closed on
Cinnamon. Measured by watching X while clicking: a 1920x1080
_NET_WM_WINDOW_TYPE_DIALOG with _NET_WM_STATE_FULLSCREEN appears, the WM moves
_NET_ACTIVE_WINDOW to it, and ~50-100 ms later it is destroyed.

The fix is Wine's per-application `Managed` setting: with Managed=N the X11
driver never asks the window manager to manage this program's windows, so the
fullscreen menu layers cannot steal activation. The program then draws its own
title bar (Wine's) instead of the desktop's, which is the visible trade-off.

Scope it per program, never prefix-wide: unmanaged windows lose the desktop's
window controls, and most programs do not need this.
"""
from .progress import null_reporter
from .wine import Prefix

def key(exe_name: str) -> str: return rf"HKCU\Software\Wine\AppDefaults\{exe_name}\X11 Driver"

def applied(p: Prefix, exe_name: str) -> bool:
    return p.reg_query(key(exe_name)).get("Managed") == "N"

def apply(p: Prefix, exe_names: list[str], reporter=None,
          why="their dropdowns are fullscreen popups the window manager would activate") -> list[str]:
    """Set Managed=N for each exe. Returns the names that changed."""
    r = null_reporter(reporter); changed = []
    for name in sorted(set(exe_names)):
        if applied(p, name): continue
        p.reg_add(key(name), "Managed", "N")
        changed.append(name)
    if changed:
        r.step(f"Window manager kept out of programs ({why})"); r.ok(", ".join(changed))
    return changed

def clear(p: Prefix, exe_name: str) -> bool:
    """Hand the program back to the window manager. True if it was set."""
    if not applied(p, exe_name): return False
    p.reg_delete_value(key(exe_name), "Managed")
    return True

def exe_name(prog) -> str:
    """The executable a Program row runs, as AppDefaults names it."""
    return prog.exe.rsplit("\\", 1)[-1] if getattr(prog, "exe", None) else ""

def supported(prog) -> bool:
    """Whether this setting can be offered for a program (it needs a launcher)."""
    return bool(exe_name(prog))

def set_for(p: Prefix, prog, on: bool) -> bool:
    """Turn the setting on or off for a Program row. True if anything changed."""
    name = exe_name(prog)
    if not name: return False
    return bool(apply(p, [name])) if on else clear(p, name)

def is_on(p: Prefix, prog) -> bool:
    name = exe_name(prog)
    return bool(name) and applied(p, name)

# What the user is choosing, in their words: the title and the explanation the
# GUI shows and the CLI prints, kept here so both say the same thing.
LABEL = "Keep the window manager away"
# Lead with "you probably do not need this": the Programs tab offers it on every
# program, and turning it on where it is not needed costs the desktop's window
# controls for nothing.
UNNECESSARY = ("Most programs do not need this. Only try it if you have a problem it describes.")
EXPLAIN = (UNNECESSARY + "\n\n"
           "It is for a program whose menus flash and close as soon as they open: its dropdowns are "
           "fullscreen popup windows, and the window manager activates them, which makes the "
           "program close its own menu. The program then draws its own title bar instead of the "
           "desktop's, and the desktop's window controls (snapping, the window menu) stop applying "
           "to it. Restart the program after changing this.")
