import unittest
from unittest import mock

from vstenv import launchguard


class FakePrefix:
    def __init__(self, running=True):
        self.running = running; self.killed = []
    def is_running(self, exe): return self.running
    def kill_exe(self, exe, wait=3.0): self.killed.append(exe); self.running = False


class LaunchGuardTest(unittest.TestCase):
    """2026-10-16: Native Access launched from the GUI hung the whole Wine
    session twice; killing it healed the session at once and a relaunch
    worked. The guard does that after every launch."""

    def _guard(self, p, probes, relaunch):
        seq = list(probes)
        return launchguard.guard(p, ("Native Access.exe",), relaunch, probe=lambda _p: seq.pop(0), sleep=lambda s: None)

    def test_healthy_launch_is_left_alone(self):
        p = FakePrefix(); calls = []
        self.assertEqual(self._guard(p, [True], lambda: calls.append(1)), "ok")
        self.assertEqual(p.killed, []); self.assertEqual(calls, [])

    def test_a_program_that_already_exited_is_not_judged(self):
        p = FakePrefix(running=False); calls = []
        self.assertEqual(self._guard(p, [], lambda: calls.append(1)), "exited")
        self.assertEqual(calls, [])

    def test_a_wedging_launch_is_killed_and_relaunched(self):
        p = FakePrefix(); calls = []
        self.assertEqual(self._guard(p, [False, True], lambda: calls.append(1)), "recovered")
        self.assertEqual(p.killed, ["Native Access.exe"]); self.assertEqual(calls, [1])

    def test_a_session_still_wedged_after_the_kill_is_reported_not_relaunched(self):
        p = FakePrefix(); calls = []
        self.assertEqual(self._guard(p, [False, False], lambda: calls.append(1)), "wedged")
        self.assertEqual(calls, [], "relaunching into a wedged session would just hang again")

    def test_an_inconclusive_probe_is_not_treated_as_a_wedge(self):
        """None = the probe could not run; never kill a user's program on that."""
        p = FakePrefix(); calls = []
        self.assertEqual(self._guard(p, [None], lambda: calls.append(1)), "ok")
        self.assertEqual(p.killed, [])

    def test_messages(self):
        self.assertIn("started again", launchguard.message("recovered", "Native Access"))
        self.assertIn("clear the wedge", launchguard.message("wedged", "Native Access"))
        self.assertIsNone(launchguard.message("ok", "x")); self.assertIsNone(launchguard.message("exited", "x"))

    def test_gui_guards_both_launch_paths(self):
        from pathlib import Path
        src = Path("vstenv/gui.py").read_text()
        self.assertEqual(src.count("self._guard_launch("), 2)


if __name__ == "__main__":
    unittest.main()
