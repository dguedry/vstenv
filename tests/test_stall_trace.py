import tempfile
import unittest
from pathlib import Path
from unittest import mock

from vstenv import stall
from vstenv.stall import Progress, StallWatch


class UsefulProgressTest(unittest.TestCase):
    """Absynth 5 5.3.4 (2026-10-13): the setup looped on an unimplemented Wine
    call, logging NtUserLockWindowUpdate thousands of times a minute. The trace
    grew by megabytes while not one file was installed, so counting bytes
    written made a spinning installer look busy for ever."""

    def test_writes_only_to_the_trace_are_not_progress(self):
        """Measured live on the spinning Absynth setup: 237 KB written in 20s,
        235 KB of it the trace."""
        before = Progress(cpu=10, written=1_000, trace=0)
        after = Progress(cpu=900, written=238_568, trace=235_873)
        self.assertFalse(before.moved(after))

    def test_wine_double_counting_the_trace_is_tolerated(self):
        """write_bytes counts the same trace write more than once (the setup
        and the wineserver serving it), so the total runs ahead of the file."""
        before = Progress(written=1_000_000, trace=500_000)
        after = Progress(written=9_000_000, trace=4_500_000)    # ~2x the growth
        self.assertFalse(before.moved(after))

    def test_writes_outside_the_trace_are_progress(self):
        before = Progress(cpu=10, written=1_000, trace=0)
        after = Progress(cpu=900, written=9_000, trace=1_000)
        self.assertTrue(before.moved(after))

    def test_cpu_alone_is_still_not_progress(self):
        """The older spinning form (Kontakt 8) burned CPU writing nothing."""
        before = Progress(cpu=10, written=1_000)
        after = Progress(cpu=9_000, written=1_000)
        self.assertFalse(before.moved(after))

    def test_no_writes_at_all_is_not_progress(self):
        self.assertFalse(Progress(written=100, trace=50).moved(Progress(written=100, trace=50)))

    def test_without_a_trace_every_write_counts(self):
        """stalled_setup watches the manager's own installs and opens no trace."""
        before = Progress(written=1_000)
        after = Progress(written=2_000)
        self.assertTrue(before.moved(after))


class WatchWithTraceTest(unittest.TestCase):
    def _watch(self, trace=None):
        self.t = 0.0
        return StallWatch(quiet_seconds=10.0, now=lambda: self.t, trace=trace)

    def test_a_spinner_filling_the_trace_is_called_stalled(self):
        with tempfile.TemporaryDirectory() as d:
            trace = Path(d) / "x.trace"
            trace.write_bytes(b"")
            w = self._watch(trace)
            # every sample: more bytes written, but all of them in the trace
            written = [1_000, 5_000, 9_000, 13_000]
            def fake(pids, tr=None):
                n = written.pop(0)
                trace.write_bytes(b"x" * n)        # the trace accounts for all of it
                return Progress(cpu=n, written=n, trace=n // 2)   # wine double-counts
            with mock.patch.object(stall, "read_progress", fake):
                self.assertFalse(w.update([1]))    # baseline
                self.t = 5;  self.assertFalse(w.update([1]))
                self.t = 20; self.assertTrue(w.update([1]), "a spinner must be called stalled")

    def test_a_real_install_is_not_called_stalled(self):
        w = self._watch()
        written = [1_000, 50_000, 900_000]
        with mock.patch.object(stall, "read_progress",
                               lambda pids, tr=None: Progress(written=written.pop(0))):
            self.assertFalse(w.update([1]))
            self.t = 20; self.assertFalse(w.update([1]))
            self.t = 40; self.assertFalse(w.update([1]), "files are landing; never call this stalled")

    def test_a_missing_trace_file_is_not_fatal(self):
        p = stall.read_progress.__wrapped__ if hasattr(stall.read_progress, "__wrapped__") else None
        with mock.patch.object(stall.host, "sh", return_value="10 20"):
            got = stall.read_progress([1], Path("/nonexistent/x.trace"))
        self.assertEqual(got.trace, 0)
        self.assertEqual(got.written, 20)


if __name__ == "__main__":
    unittest.main()
