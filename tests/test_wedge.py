import unittest
from unittest import mock
from pathlib import Path
from vstenv import wedge


class FakePrefix:
    def __init__(self): self.path = Path("/tmp/prefix"); self.killed = False
    def kill(self): self.killed = True


class WedgeDetectTest(unittest.TestCase):
    """The 2026-10-01 wedge: every DirectComposition program hung windowless at
    0% CPU after dcomp.dll loaded, and only `wineserver -k` cleared it. The
    checks are conservative on purpose -- clearing it closes the user's running
    plugins, so a false positive costs them work."""

    def _run(self, cpu_samples, windowed, ages=None):
        """Drive the detector with scripted /proc and window readings."""
        samples = list(cpu_samples)
        def fake_cpu(_path): return samples.pop(0) if len(samples) > 1 else samples[0]
        return mock.patch.object(wedge, "_cpu", fake_cpu), \
               mock.patch.object(wedge, "_windowed_pids", lambda: set(windowed)), \
               mock.patch.object(wedge, "_age", lambda pid: (ages or {}).get(pid, 10.0)), \
               mock.patch.object(wedge.time, "sleep", lambda _s: None)

    def test_healthy_app_with_a_window_is_not_stuck(self):
        a, b, c, d = self._run([{101: 500}, {101: 900}], windowed={101})
        with a, b, c, d:
            self.assertEqual(wedge.stuck_processes(FakePrefix()), [])

    def test_starting_app_burning_cpu_is_not_stuck(self):
        """Windowless but busy: it is loading, not wedged."""
        a, b, c, d = self._run([{101: 100}, {101: 450}], windowed=set())
        with a, b, c, d:
            self.assertEqual(wedge.stuck_processes(FakePrefix()), [])

    def test_windowless_and_idle_is_stuck(self):
        a, b, c, d = self._run([{101: 300}, {101: 300}], windowed=set())
        with a, b, c, d:
            self.assertEqual(wedge.stuck_processes(FakePrefix()), [101])

    def test_two_stuck_programs_are_the_wedge(self):
        a, b, c, d = self._run([{101: 1, 102: 2}, {101: 1, 102: 2}], windowed=set())
        with a, b, c, d:
            self.assertEqual(sorted(wedge.detect(FakePrefix())), [101, 102])

    def test_one_young_stuck_program_is_not_called_the_wedge(self):
        """A single slow launch must not trigger a session restart."""
        a, b, c, d = self._run([{101: 1}, {101: 1}], windowed=set(), ages={101: 5.0})
        with a, b, c, d:
            self.assertEqual(wedge.detect(FakePrefix()), [])

    def test_one_long_stuck_program_is_the_wedge(self):
        a, b, c, d = self._run([{101: 1}, {101: 1}], windowed=set(),
                               ages={101: wedge.SOLO_GRACE + 1})
        with a, b, c, d:
            self.assertEqual(wedge.detect(FakePrefix()), [101])

    def test_unreadable_window_list_is_inconclusive(self):
        """No display or no wmctrl -> None, meaning "cannot judge". An *empty*
        set is different: that is the wedge, where nothing drew a window."""
        a, b, c, d = self._run([{101: 1}, {101: 1}], windowed=set())
        with a, b, mock.patch.object(wedge, "_windowed_pids", lambda: None), d:
            self.assertEqual(wedge.stuck_processes(FakePrefix()), [])

    def test_no_dcomp_processes_at_all(self):
        a, b, c, d = self._run([{}], windowed={1})
        with a, b, c, d:
            self.assertEqual(wedge.stuck_processes(FakePrefix()), [])

    def test_clear_bounces_the_wineserver(self):
        p = FakePrefix()
        self.assertTrue(wedge.clear(p))
        self.assertTrue(p.killed)


if __name__ == "__main__":
    unittest.main()
