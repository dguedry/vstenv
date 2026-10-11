"""The desktop shell stops mapping Wine windows.

Reported by a user on Fedora 44 / GNOME: a FabFilter installer ran, had a dock
entry and answered the keyboard, but its window never appeared. Its
close-confirmation dialog was visible -- so the program was alive and creating
windows, and something between Wine and the screen was refusing to show them.

That is a known GNOME/mutter failure, not a Wine or vstenv one. Mutter's
"Application is not responding" dialog throws on a window that has no surface
yet, which can leave a window stuck in `unmanaging`; from then on mutter
answers every map request with `WM_STATE=Iconic`, and Wine does as it is told
and minimises the window to -32000,-32000. It persists for the rest of the
session and affects every Wine window, so the next program looks broken too.

Nothing in the prefix can fix it: a window Wine has hidden on the Win32 side
cannot be brought back from outside, and forging WM_STATE only fools whoever
reads it. Logging out and back in clears it.

This module only recognises the state, so the app can say which of the two it
is instead of leaving someone to wonder whether their installer is broken.
"""
from __future__ import annotations

import os

from . import host

# Mutter logs this for the rest of the session once a window is stuck.
_PING_ASSERT = "meta_window_can_ping"
# Enough to mean "this is happening", not a one-off from an app that really hung.
BUSY_THRESHOLD = 20


def desktop() -> str:
    """The desktop environment's name, lowercased, or ''.

    The variable is not always there to read. Flatpak only passes
    XDG_CURRENT_DESKTOP through when the launching environment had it, so a
    run from a terminal, a service or ssh sees nothing -- measured on a Fedora
    GNOME host, where the sandbox saw None while the session had GNOME. Ask
    the host's own session when the variable is missing, rather than quietly
    deciding this is not a mutter desktop and never looking again."""
    d = (os.environ.get("XDG_CURRENT_DESKTOP") or "").strip()
    if d: return d.lower()
    out = host.sh("systemctl --user show-environment 2>/dev/null "
                  "| sed -n 's/^XDG_CURRENT_DESKTOP=//p'", timeout=20)
    return out.strip().splitlines()[0].lower() if out.strip() else ""


def on_mutter() -> bool:
    """GNOME and the other mutter-based shells this affects."""
    d = desktop()
    return any(k in d for k in ("gnome", "pantheon", "budgie"))


def ping_assertions() -> int:
    """How many times mutter has logged the stuck-window assertion this boot.

    0 when there is no journal to read, which is also what a healthy session
    gives -- the count only matters next to the symptom."""
    out = host.sh(
        f"journalctl --user -b --no-pager 2>/dev/null | grep -c {_PING_ASSERT} || echo 0",
        timeout=30)
    for line in out.splitlines():
        line = line.strip()
        if line.isdigit(): return int(line)
    return 0


def detect() -> dict:
    """Whether the shell looks wedged. {'wedged': bool, 'count': int, 'why': str}.

    Deliberately conservative: it only says yes on a mutter desktop with many
    assertions, because the honest answer on a quiet session is "no reason to
    think so"."""
    if not on_mutter():
        return {"wedged": False, "count": 0,
                "why": f"not a mutter desktop ({desktop() or 'unknown'})"}
    n = ping_assertions()
    if n >= BUSY_THRESHOLD:
        return {"wedged": True, "count": n,
                "why": (f"mutter logged {_PING_ASSERT} {n} times this session: it has a window stuck "
                        "in unmanaging and is answering every Wine map request with WM_STATE=Iconic, "
                        "so Wine minimises its windows off-screen. Log out and back in; nothing in "
                        "the prefix can fix it")}
    return {"wedged": False, "count": n, "why": "mutter has not logged the stuck-window assertion"}


def note() -> str:
    """One line for a user who cannot see a window that should be there."""
    d = detect()
    if d["wedged"]: return d["why"]
    if on_mutter():
        return ("If a program runs but never shows a window on GNOME, check whether the shell is "
                "wedged: journalctl --user -b | grep -c meta_window_can_ping. A large count means "
                "logging out and back in is the only fix")
    return ""
