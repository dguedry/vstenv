"""Noticing an NI installer that has stopped making progress.

NI's InstallAware setups sometimes stop dead under Wine: the payload is
extracted (gigabytes on disk) and then the process sits at zero CPU forever,
querying an MSI virtual table Wine's SQL parser rejects. It never exits, so an
exit code never arrives and a caller waiting on it waits for its timeout --
an hour in `install_app`, which users reasonably read as "hangs indefinitely".

A second form spins instead: the inner setup burns half a core in a request
loop against wineserver, again writing nothing, forever (Kontakt 8 8.13.1,
2026-09-25). So CPU time is not evidence of progress; bytes written are.

A third form writes while getting nowhere (Absynth 5 5.3.4, 2026-10-13): the
setup loops on an unimplemented call and Wine logs a FIXME every time round,
so the trace we opened for it grows by megabytes a minute while not one file
is installed. Bytes written are not evidence either when the only thing being
written is our own trace, so the watcher is told which file that is and
subtracts it.

This watches a running installer and reports when it has plainly stopped. The
rule is deliberately conservative, because acting on a false positive (killing
an installer that is merely slow) is worse than waiting: progress is *any* byte
written by any of the watched processes (the setup stubs and the msiexec that
does the copying for them), and it must be absent for several consecutive
minutes before we call it stalled. Slow disks, big payloads and Wine's own
sluggishness all still write something along the way.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

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
    trace: int = 0      # of those, bytes written to the trace we opened

    def moved(self, other: "Progress") -> bool:
        """True when `other` shows real progress over this reading.

        Compares how much was written between the two samples against how much
        the trace grew in the same interval. Absolute totals cannot be
        subtracted from one another: write_bytes counts every write by every
        watched process, and Wine accounts the same trace write more than once
        (the setup and the wineserver that serves it), so the total runs well
        ahead of the file's size. The growth of each over one interval is
        comparable, and that is what decides.

        A spinner's writes are all trace, so the two grow together and this is
        False. A real install writes files as well, so writes outrun the trace
        and this is True."""
        wrote = other.written - self.written
        traced = other.trace - self.trace
        if wrote <= 0: return False
        # Wine double-counts trace writes, so allow the trace to explain up to
        # twice its own growth before a write is credited as real progress.
        return wrote > traced * 2

def read_progress(pids: list[int], trace=None) -> Progress:
    """CPU and bytes-written totals for these pids, 0 when they cannot be read.

    `trace` is the file the caller is capturing the setup's own output into;
    its size is reported separately so it can be discounted."""
    if not pids: return Progress()
    out = host.sh(_PROGRESS_SH, env={"PIDS": " ".join(str(p) for p in pids)}).split()
    size = 0
    if trace is not None:
        try: size = Path(trace).stat().st_size
        except OSError: size = 0
    try: return Progress(int(out[0]), int(out[1]), size)
    except (IndexError, ValueError): return Progress()

class StallWatch:
    """Tracks whether a set of processes is still doing anything.

    `quiet_seconds` is how long everything must be still before `stalled()`
    turns true. The default is generous on purpose: an installer decompressing
    a large payload on a slow disk can be quiet for a while, and being wrong
    here costs the user a working install.
    """

    def __init__(self, quiet_seconds: float = 300.0, now=time.monotonic, trace=None):
        self.quiet_seconds = quiet_seconds
        self.trace = trace          # the setup's own output file, not progress
        self._now = now
        self._last = Progress()
        self._since = None      # when we first saw no movement
        self._started = now()

    def update(self, pids: list[int]) -> bool:
        """Feed the current pids. Returns True once they look stalled.

        Writes to the trace file this watch was given do not count: a setup
        looping on a Wine stub fills it without installing anything."""
        if not pids:                        # nothing running: finished or died, not a stall
            self._since = None
            return False
        p = read_progress(pids, self.trace)
        if p == Progress():                 # /proc unreadable: no information, never guess
            self._since = None
            return False
        if self._last == Progress():        # first reading: a baseline, not a judgement
            self._last = p
            self._since = self._now()
            return False
        if self._last.moved(p):             # a byte written somewhere other than the trace counts as alive
            self._since = None
        elif self._since is None:
            self._since = self._now()
        self._last = p
        return self.stalled()

    def stalled(self) -> bool:
        return self._since is not None and (self._now() - self._since) >= self.quiet_seconds

    def quiet_for(self) -> float:
        return 0.0 if self._since is None else self._now() - self._since
