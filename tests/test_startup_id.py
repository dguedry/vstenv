import os
import unittest
from unittest import mock
from pathlib import Path


class StartupIdTest(unittest.TestCase):
    """2026-10-18: new Wine windows took the screen over whatever the user was
    doing. A window with no _NET_WM_USER_TIME is one the WM focuses on sight;
    Wine takes that time from DESKTOP_STARTUP_ID, which no vstenv launch set."""

    def _prefix(self):
        from vstenv import wine
        import types
        p = wine.Prefix.__new__(wine.Prefix)
        p.path = Path("/tmp/prefix"); p.build = types.SimpleNamespace(root=Path("/tmp/w"))
        p.dll_overrides = lambda: "x=d"
        return p

    def test_wine_env_forwards_the_startup_id(self):
        p = self._prefix()
        with mock.patch.dict(os.environ, {"DESKTOP_STARTUP_ID": "cinnamon-123_TIME456"}, clear=False):
            self.assertEqual(p.wine_env()["DESKTOP_STARTUP_ID"], "cinnamon-123_TIME456")

    def test_wine_env_without_one_sets_none(self):
        p = self._prefix()
        env = {k: v for k, v in os.environ.items() if k != "DESKTOP_STARTUP_ID"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertNotIn("DESKTOP_STARTUP_ID", p.wine_env())

    def test_gui_wraps_both_launch_paths(self):
        src = Path("vstenv/gui.py").read_text()
        self.assertEqual(src.count("self.with_startup_id("), 2)
        self.assertIn("get_user_time()", src)
        self.assertIn("_TIME{t}", src)
