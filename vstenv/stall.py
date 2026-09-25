"""Noticing an NI installer that has stopped making progress.

NI's InstallAware setups sometimes stop dead under Wine: the payload is
extracted (gigabytes on disk) and then the process sits at zero CPU forever,
querying an MSI virtual table Wine's SQL parser rejects. It never exits, so an
exit code never arrives and a caller waiting on it waits for its timeout --
an hour in `install_app`, which users reasonably read as "hangs indefinitely".

This watches a running installer and reports when it has plainly stopped. The
rule is deliberately conservative, because acting on a false positive (killing
an installer that is merely slow) is worse than waiting: progress counts as
*any* CPU time used or *any* byte written, and it must be absent for several
consecutive minutes before we call it stalled. Slow disks, big payloads and
Wine's own sluggishness all still count as progress.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from . import host

# Read CPU jiffies (utime+stime) and bytes written for a pid and its children.
# /proc inside a Flatpak sandbox only shows the sandbox, so this runs on the host.
_PROGRESS_SH = r'''
total_cpu=0; total_io=0
for pid in $PIDS; do
  [ -r "/proc/$pid/stat" ] || continue
  set -- $(sed 's/.*) //' "/proc/$pid/stat" 2>/dev/null)
  # after the comm field: state is $1, so utime is $12 and stime $13
  u=${12:-0}; s=${13:-0}
  total_cpu=$((total_cpu + u + s))
  w=$(awk '/^write_bytes:/{print $2}' "/proc/$pid/io" 2>/dev/null)
  [ -n "$w" ] && total_io=$((total_io + w))
done
echo "$total_cpu $total_io"
'''

@dataclass
class Progress:
    cpu: int = 0        # jiffies used, cumulative
    written: int = 0    # bytes written, cumulative

    def moved(self, other: "Progress") -> bool:
        return other.cpu > self.cpu or other.written > self.written

def read_progress(pids: list[int]) -> Progress:
    """CPU and bytes-written totals for these pids, 0 when they cannot be read."""
    if not pids: return Progress()
    out = host.sh(_PROGRESS_SH, env={"PIDS": " ".join(str(p) for p in pids)}).split()
    try: return Progress(int(out[0]), int(out[1]))
    except (IndexError, ValueError): return Progress()

class StallWatch:
    """Tracks whether a set of processes is still doing anything.

    `quiet_seconds` is how long everything must be still before `stalled()`
    turns true. The default is generous on purpose: an installer decompressing
    a large payload on a slow disk can be quiet for a while, and being wrong
    here costs the user a working install.
    """

    def __init__(self, quiet_seconds: float = 300.0, now=time.monotonic):
        self.quiet_seconds = quiet_seconds
        self._now = now
        self._last = Progress()
        self._since = None      # when we first saw no movement
        self._started = now()

    def update(self, pids: list[int]) -> bool:
        """Feed the current pids. Returns True once they look stalled."""
        if not pids:                        # nothing running: finished or died, not a stall
            self._since = None
            return False
        p = read_progress(pids)
        if p == Progress():                 # /proc unreadable: no information, never guess
            self._since = None
            return False
        if self._last == Progress():        # first reading: a baseline, not a judgement
            self._last = p
            self._since = self._now()
            return False
        if self._last.moved(p):             # any CPU or any byte written counts as alive
            self._since = None
        elif self._since is None:
            self._since = self._now()
        self._last = p
        return self.stalled()

    def stalled(self) -> bool:
        return self._since is not None and (self._now() - self._since) >= self.quiet_seconds

    def quiet_for(self) -> float:
        return 0.0 if self._since is None else self._now() - self._since
