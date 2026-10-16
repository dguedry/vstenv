"""Waiting for a launched program's first window.

A Wine program takes a few seconds to draw its first window, and the GUI has
nothing to show for the click until then. This watches for the moment: the
launched program's pids (a host /proc scan) against the pids that own a
mapped X window (wmctrl on the host, the same list the wedge detector reads).
"""
from __future__ import annotations

import time

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
