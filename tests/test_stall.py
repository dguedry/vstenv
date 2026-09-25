"""A stalled installer must be recognised, but only after it has plainly stopped:
acting on a false positive would kill an install that was merely slow."""
import unittest
from unittest import mock

from vstenv import stall

class StallWatchTest(unittest.TestCase):
    def _watch(self, quiet=30):
        self.t = [0.0]
        return stall.StallWatch(quiet_seconds=quiet, now=lambda: self.t[0])

    def _feed(self, w, readings):
        with mock.patch.object(stall, "read_progress", side_effect=readings):
            return [w.update([123]) for _ in readings]

    def test_quiet_for_long_enough_is_a_stall(self):
        w = self._watch()
        with mock.patch.object(stall, "read_progress", return_value=stall.Progress(10, 100)):
            self.assertFalse(w.update([123]))          # baseline
            self.t[0] = 29; self.assertFalse(w.update([123]))
            self.t[0] = 31; self.assertTrue(w.update([123]))

    def test_cpu_progress_resets_the_clock(self):
        w = self._watch()
        with mock.patch.object(stall, "read_progress", side_effect=[stall.Progress(10, 100), stall.Progress(11, 100), stall.Progress(11, 100)]):
            w.update([123])
            self.t[0] = 25; self.assertFalse(w.update([123]))    # used CPU: alive
            self.t[0] = 50; self.assertFalse(w.update([123]))    # only 25s quiet since
            self.assertLess(w.quiet_for(), 30)

    def test_disk_writes_count_as_progress(self):
        """An installer unpacking gigabytes may use little CPU; bytes written count."""
        w = self._watch()
        with mock.patch.object(stall, "read_progress", side_effect=[stall.Progress(10, 100), stall.Progress(10, 5_000_000), stall.Progress(10, 5_000_000)]):
            w.update([123])
            self.t[0] = 25; self.assertFalse(w.update([123]))
            self.t[0] = 45; self.assertFalse(w.update([123]))

    def test_no_processes_is_not_a_stall(self):
        w = self._watch()
        self.assertFalse(w.update([]))                 # it finished or died
        self.assertFalse(w.stalled())

    def test_unreadable_proc_never_reports_a_stall(self):
        """Inside a sandbox /proc may be unreadable; no information must not mean 'kill it'."""
        w = self._watch()
        with mock.patch.object(stall, "read_progress", return_value=stall.Progress()):
            w.update([123]); self.t[0] = 600
            self.assertFalse(w.update([123]))
            self.assertFalse(w.stalled())

    def test_default_is_conservative(self):
        self.assertGreaterEqual(stall.StallWatch().quiet_seconds, 120)

class ProgressTest(unittest.TestCase):
    def test_moved(self):
        self.assertTrue(stall.Progress(1, 0).moved(stall.Progress(2, 0)))
        self.assertTrue(stall.Progress(1, 0).moved(stall.Progress(1, 5)))
        self.assertFalse(stall.Progress(2, 5).moved(stall.Progress(2, 5)))

if __name__ == "__main__": unittest.main()
