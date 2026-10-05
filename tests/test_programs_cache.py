import time
import unittest
from unittest import mock
from pathlib import Path
from vstenv import programs


class FakePrefix:
    def __init__(self, path="/tmp/p"): self.path = Path(path)


class ProgramsCacheTest(unittest.TestCase):
    """installed() is asked for several times in one GUI refresh (Plugins page,
    Programs page, and dcomp.program_exes walking it), and each build reads the
    registry and Start Menu -- about two seconds. It is cached briefly."""

    def setUp(self): programs.invalidate()
    def tearDown(self): programs.invalidate()

    def test_second_call_does_not_rebuild(self):
        calls = []
        with mock.patch.object(programs, "_installed_uncached", lambda p: calls.append(1) or ["x"]):
            self.assertEqual(programs.installed(FakePrefix()), ["x"])
            self.assertEqual(programs.installed(FakePrefix()), ["x"])
        self.assertEqual(len(calls), 1)

    def test_fresh_forces_a_rebuild(self):
        calls = []
        with mock.patch.object(programs, "_installed_uncached", lambda p: calls.append(1) or ["x"]):
            programs.installed(FakePrefix())
            programs.installed(FakePrefix(), fresh=True)
        self.assertEqual(len(calls), 2)

    def test_invalidate_forces_a_rebuild(self):
        """An install or removal must not be hidden by the cache."""
        calls = []
        with mock.patch.object(programs, "_installed_uncached", lambda p: calls.append(1) or ["x"]):
            programs.installed(FakePrefix())
            programs.invalidate()
            programs.installed(FakePrefix())
        self.assertEqual(len(calls), 2)

    def test_a_different_prefix_is_cached_separately(self):
        calls = []
        with mock.patch.object(programs, "_installed_uncached", lambda p: calls.append(str(p.path)) or [str(p.path)]):
            programs.installed(FakePrefix("/tmp/a"))
            programs.installed(FakePrefix("/tmp/b"))
        self.assertEqual(calls, ["/tmp/a", "/tmp/b"])

    def test_the_entry_expires(self):
        calls = []
        with mock.patch.object(programs, "_installed_uncached", lambda p: calls.append(1) or ["x"]), \
             mock.patch.object(programs, "_CACHE_TTL", 0.0):
            programs.installed(FakePrefix())
            time.sleep(0.01)
            programs.installed(FakePrefix())
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
