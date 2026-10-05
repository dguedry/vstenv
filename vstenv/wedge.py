"""Detect and clear the session-wide DirectComposition wedge.

Seen 2026-10-01: after a plugin load went wrong, *every* program that draws
through DirectComposition (SWAM standalones, HALion, the Spitfire app) hung
windowless at 0% CPU right after dcomp.dll loaded. Killing the hung processes
did not help -- the state lived in the wineserver session, so only a
`wineserver -k` cleared it. The cost was an evening of chasing DLL contents
that turned out to be fine.

A wedged process looks like: dcomp.dll mapped, no window, no CPU. One such
process is just a program still starting up; what makes it the session wedge
is that *several* are stuck at once, or one has been stuck well past any
reasonable startup. Both checks are deliberately conservative -- bouncing the
wineserver kills the user's running plugins, so a false positive costs them
work.
"""
from __future__ import annotations

import time

from . import host
from .progress import null_reporter

# dcomp.dll mapped, plus utime+stime from /proc/<pid>/stat (fields 14 and 15
# after the comm field, which is why the cut on ')' comes first -- a process
# name can contain spaces).
_SCAN = r"""for p in /proc/[0-9]*; do
  pid=${p#/proc/}
  [ -r "$p/stat" ] || continue
  tr '\0' '\n' < "$p/environ" 2>/dev/null | grep -qxF -- "WINEPREFIX=$VSTENV_PREFIX" || continue
  grep -qi 'dcomp\.dll' "$p/maps" 2>/dev/null || continue
  set -- $(cut -d')' -f2- "$p/stat" 2>/dev/null)
  printf '%s %s\n' "$pid" "$((${12} + ${13}))"
done"""

SETTLE = 4.0          # seconds of CPU observation
MIN_STUCK = 2         # this many windowless, idle dcomp processes = a wedge
SOLO_GRACE = 90.0     # or one of them stuck this long (a slow first launch is fine)


def _cpu(prefix_path) -> dict[int, int]:
    """{pid: cpu_ticks} for this prefix's processes that have dcomp.dll mapped."""
    out = {}
    for line in host.sh(_SCAN, env={"VSTENV_PREFIX": str(prefix_path)}).splitlines():
        pid, _, ticks = line.partition(" ")
        if pid.isdigit() and ticks.strip().isdigit(): out[int(pid)] = int(ticks)
    return out


def _windowed_pids() -> set[int] | None:
    """Pids owning a mapped X11 window, or None when the window list cannot be
    read at all (no display, no wmctrl). None means "cannot judge" -- distinct
    from an empty set, which is what the wedge itself looks like once every
    program has failed to draw."""
    out = host.sh("wmctrl -l -p 2>/dev/null; echo __rc=$?")
    if "__rc=0" not in out: return None
    pids = set()
    for line in out.splitlines():
        if line.startswith("__rc="): continue
        f = line.split()
        if len(f) >= 3 and f[2].isdigit(): pids.add(int(f[2]))
    return pids


def _age(pid: int) -> float:
    """Seconds since the process started, or 0.0 if it cannot be read."""
    out = host.sh(f"ps -o etimes= -p {int(pid)} 2>/dev/null || true").strip()
    try: return float(out)
    except ValueError: return 0.0


def stuck_processes(p) -> list[int]:
    """Pids of this prefix's dcomp programs that are windowless and burning no
    CPU. Samples twice: a program mid-startup is busy, and the wedge is not."""
    first = _cpu(p.path)
    if not first: return []
    windowed = _windowed_pids()
    if windowed is None: return []      # no window list to judge by -- say nothing
    candidates = [pid for pid in first if pid not in windowed]
    if not candidates: return []
    time.sleep(SETTLE)
    second = _cpu(p.path)
    later = _windowed_pids()            # it may have drawn its window meanwhile
    if later is None: return []
    return [pid for pid in candidates
            if pid in second and pid not in later and second[pid] == first[pid]]


def detect(p) -> list[int]:
    """The stuck pids when they amount to the session wedge, else []."""
    stuck = stuck_processes(p)
    if len(stuck) >= MIN_STUCK: return stuck
    if len(stuck) == 1 and _age(stuck[0]) >= SOLO_GRACE: return stuck
    return []


def clear(p, reporter=None) -> bool:
    """Bounce the wineserver, which is the only thing that clears this. Returns
    True if it was done. The caller decides whether the moment is right: this
    ends every Windows program in the prefix, a DAW's plugin hosts included."""
    r = null_reporter(reporter)
    r.step("Clearing the DirectComposition wedge (restarting the Wine session)")
    try:
        p.kill()
    except Exception as e:
        r.fail(str(e)[:100]); return False
    r.ok("Wine session restarted; reopen the program")
    return True


def check(p, reporter=None, fix=False) -> list[int]:
    """Report the wedge, and clear it when asked. Returns the stuck pids."""
    r = null_reporter(reporter)
    stuck = detect(p)
    if not stuck: return []
    r.step("DirectComposition wedge")
    r.log(f"{len(stuck)} program(s) hung windowless after loading dcomp.dll "
          f"(pids {', '.join(str(s) for s in stuck)}).")
    r.log("Every DirectComposition program stays hung until the Wine session is restarted.")
    if fix: clear(p, r)
    else: r.log("Run `vstenv doctor --fix` to restart it (this closes running plugins).")
    return stuck
