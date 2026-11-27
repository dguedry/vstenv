"""A stale virtual-desktop monitor in Wine's display cache.

Running `wine explorer /desktop=<name>,<W>x<H>` -- a sized virtual desktop --
makes winex11 record a monitor named "Default_Monitor" of that size in the
display cache the whole prefix shares (HKLM\\System\\...\\Enum\\DISPLAY). The
cache is not re-detected on the next session, nor by wineboot: afterwards
every program on the normal desktop believes the screen is W x H. Seen
2026-10-16 after a 640x480 probe on a 1920x1080 host: windows clamped to
640x480, Native Access painting a 640x512 corner of its 1024x768 window.

Any RandR change event makes winex11 re-enumerate the real monitors and
rewrite the cache, which is the fix; so does logging out and back in.
"""
from __future__ import annotations

import re

_KEY = re.compile(r"^\[System\\\\ControlSet001\\\\Enum\\\\DISPLAY\\\\Default_Monitor\\\\", re.M)
_OTHER = re.compile(r"^\[System\\\\ControlSet001\\\\Enum\\\\DISPLAY\\\\(?!Default_Monitor\\\\)[^\\]+\\\\", re.M)

def stale_virtual_monitor(p) -> bool:
    """Whether the display cache carries a virtual desktop's monitor.

    "Default_Monitor" alone is not enough: on Xwayland (no EDID) Wine names the
    one real monitor "Default_Monitor" too. The stale one sits beside the real
    monitors, so it counts only when another monitor is cached as well.
    Limits: a virtual desktop cached on a host without EDID is not seen; a laptop prefix
    used docked on X11 and later on Xwayland keeps a real monitor beside Default_Monitor
    and is flagged although nothing is stale. The key timestamps (last write) do not tell
    the two apart: in both cases Default_Monitor is the newer entry."""
    try: text = (p.path / "system.reg").read_text(errors="replace")
    except OSError: return False
    return bool(_KEY.search(text) and _OTHER.search(text))

FIX = ("re-detect the display: change the screen mode and back (xrandr --output <output> --mode <current mode>), "
       "or log out and back in; then start the program again")
