"""Waiting for a launched program's first window.

A Wine program takes a few seconds to draw its first window, and the GUI has
nothing to show for the click until then. This watches for the moment: the
launched program's pids (a host /proc scan) against the pids that own a
mapped X window (wmctrl on the host, the same list the wedge detector reads).
"""
from __future__ import annotations

import threading
import time

from . import host

TIMEOUT = 30.0      # a program that has not drawn by then is not about to; stop waiting
GRACE = 3.0         # seconds before "no process" counts as "it exited" (the spawn takes a moment)

def wait_for_window(pids_of, windowed, timeout: float = TIMEOUT, sleep=time.sleep, now=time.monotonic) -> str:
    """Block until the program draws a window. Returns 'window', 'exited'
    (its processes are gone), 'timeout', or 'unknown' (the window list cannot
    be read, so there is nothing to wait on)."""
    t0 = now()
    while True:
        pids = set(pids_of())
        if not pids and now() - t0 >= GRACE: return "exited"
        w = windowed()
        if w is None: return "unknown"
        if pids & w: return "window"
        if now() - t0 >= timeout: return "timeout"
        sleep(0.7)


def resync_if_maximized(win: str, sh=host.sh) -> bool:
    """A Wine window the window manager maximized as it appeared has Wine's
    idea of its position out of step with X's, and every pointer event lands
    off by that difference. Gig Performer 5 (2026-10-18): it asked for a
    1920x1008 client, Cinnamon maximized it in place and moved it to 0,32,
    Wine kept its own placement at 258,50 -- the move came with no size
    change, which Wine ignores -- and hover and clicks were 258 px to the
    left of the pointer. Toggling the maximized state makes Wine re-read the
    geometry; verified to put Wine's client origin at 0,32, matching X.

    Returns True when the window was maximized and the toggle was done."""
    state = sh(f"xprop -id {win} _NET_WM_STATE 2>/dev/null")
    if "_NET_WM_STATE_MAXIMIZED" not in state: return False
    sh(f"wmctrl -i -r {win} -b remove,maximized_vert,maximized_horz; sleep 0.4; "
       f"wmctrl -i -r {win} -b add,maximized_vert,maximized_horz")
    return True


def watch_and_resync(p, exe_names, windowed=None, windows_of=None) -> threading.Thread:
    """Start a daemon thread that waits for the program's first window and, if
    the window manager maximized it on sight, toggles the maximized state so
    Wine re-reads the geometry (resync_if_maximized). Attached to every launch
    by programs.run, so the GUI, `vstenv run` and the desktop-menu entries all
    get it; the CLI joins the thread so it is not killed by exiting."""
    from . import wedge
    exe_names = tuple(x for x in exe_names if x)
    windowed = windowed or wedge.windowed_pids; windows_of = windows_of or wedge.windows_of
    def pids(): return {pid for x in exe_names for pid, _ in p.processes(x)}
    def work():
        try:
            if exe_names and wait_for_window(pids, windowed) == "window":
                for win in windows_of(pids()): resync_if_maximized(win)
        except Exception: pass
    t = threading.Thread(target=work, daemon=True, name="window-watch")
    t.start(); return t
