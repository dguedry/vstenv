"""Recovering a launch that wedges the Wine session.

Twice on 2026-10-16 a program started from the GUI (Native Access, both
times) never showed a window, and from that moment no new program in the
prefix could: each stopped in user32's process attach waiting for the
session's desktop. Console Wine was fine. The one reliable observation was
that killing the program that had just been launched healed the session at
once, and launching it again then worked -- every launch driven by hand
afterwards did. The Wine-side cause (an explorer that tears its desktop down
and never exits, by the look of it) is not pinned down; see wedge.py for the
detector this builds on.

So after a launch, wait a little, and if the session no longer answers the
probe, do what worked: kill what was just launched, make sure the session
answers again, and launch once more. If it does not answer even then, the
session needs `vstenv wedge clear`, and the caller says so.
"""
from __future__ import annotations

import time

from . import wedge

DELAY = 20.0    # seconds after a launch before judging; a slow start is not a wedge

def guard(p, exe_names, relaunch, delay: float = DELAY, probe=wedge.probe_session, sleep=time.sleep) -> str:
    """Watch a launch and recover it. Returns one of:
    'ok'        the session still answers (or nothing to judge),
    'exited'    the program already ended, so nothing to judge,
    'recovered' it had wedged the session; killed and launched again,
    'wedged'    killed it, and the session still does not answer: clear it."""
    sleep(delay)
    if not any(p.is_running(x) for x in exe_names): return "exited"
    if probe(p) is not False: return "ok"
    for x in exe_names: p.kill_exe(x, wait=2.0)
    if probe(p) is False: return "wedged"
    relaunch()
    return "recovered"

def message(result: str, name: str) -> str | None:
    """What to tell the user, or None when there is nothing to say."""
    if result == "recovered":
        return f"{name} hung the Wine session while starting; it was closed and started again"
    if result == "wedged":
        return f"{name} hung the Wine session and closing it did not help: use Health → clear the wedge"
    return None
