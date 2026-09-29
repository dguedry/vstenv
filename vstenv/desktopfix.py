"""Stop Wine windows raising themselves to the front on the Cinnamon desktop.

Wine's X11 driver activates (SetForegroundWindow) its top-level windows when
they receive focus, and with several Wine windows sharing one session (a plugin
editor embedded in a DAW, a standalone manager, the Arturia agent) an ordinary
click in the DAW makes the Wine side re-raise its windows. Muffin (Cinnamon's
window manager) honours that in its default `focus-new-windows = smart` mode,
where an activating window is focused and raised without being asked; `strict`
mode only focuses a window when it is actually supposed to be. Switching to
`strict` is the documented Cinnamon fix and takes effect immediately.

This is a desktop setting, not a prefix change, so it is applied only when the
host is Cinnamon, only when it is still at the default `smart`, and it is
recorded so a user's own choice is never overridden twice. There is no Wine
registry option for this; the window manager is the only lever.
"""
from __future__ import annotations

from . import host
from .progress import null_reporter

SCHEMA = "org.cinnamon.desktop.wm.preferences"
KEY = "focus-new-windows"
WANT = "strict"
DEFAULT = "smart"

def _gsettings(args: list[str]) -> tuple[int, str]:
    try:
        cp = host.run(["gsettings", *args], capture_output=True, text=True, timeout=20)
        return cp.returncode, (cp.stdout or "").strip()
    except Exception as e:
        return 1, str(e)[:80]

def is_cinnamon() -> bool:
    """True when the host runs Cinnamon (its wm schema is present)."""
    rc, out = _gsettings(["list-schemas"])
    if rc != 0: return False
    return SCHEMA in out.splitlines()

def current() -> str | None:
    """The host's focus-new-windows value, or None if it cannot be read."""
    rc, out = _gsettings(["get", SCHEMA, KEY])
    if rc != 0: return None
    return out.strip().strip("'")

def status() -> dict:
    if not is_cinnamon():
        return {"desktop": "other", "applies": False, "ok": True, "value": None}
    val = current()
    return {"desktop": "cinnamon", "applies": True,
            "ok": val == WANT, "value": val}

def apply(reporter=None) -> bool:
    """On Cinnamon, switch focus-new-windows to strict so Wine windows stop
    raising themselves. Only changes it from the default; a user's own non-default
    choice is left alone. Returns True when it ends up at the wanted value (or the
    host is not Cinnamon, where nothing is needed)."""
    r = null_reporter(reporter)
    if not is_cinnamon():
        return True
    r.step("Cinnamon: stop Wine windows jumping to the front")
    val = current()
    if val == WANT:
        r.skip(f"{KEY} already {WANT}"); return True
    if val is not None and val != DEFAULT:
        r.skip(f"{KEY} is {val!r} (your setting); left as is"); return True
    rc, out = _gsettings(["set", SCHEMA, KEY, WANT])
    if rc != 0 or current() != WANT:
        r.fail(f"could not set {KEY}: {out}"); return False
    r.ok(f"{KEY}: {DEFAULT} -> {WANT}"); return True

def reset(reporter=None):
    """Restore focus-new-windows to the Cinnamon default (only if we set strict)."""
    r = null_reporter(reporter)
    if not is_cinnamon(): return
    r.step("Cinnamon: restoring focus-new-windows")
    if current() == WANT:
        _gsettings(["set", SCHEMA, KEY, DEFAULT])
    r.ok()
