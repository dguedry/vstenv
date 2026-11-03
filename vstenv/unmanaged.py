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
