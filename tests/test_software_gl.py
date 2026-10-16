import os
import unittest
from unittest import mock

from vstenv import webview2


class SoftwareGLTest(unittest.TestCase):
    """Reported on Fedora 44 + niri (Wayland/XWayland), AMD RX 9070 XT: the
    Software Center's window downloaded its page and never painted -- white
    turning black, no input -- with the presentation flags already in effect.
    Software GL fixed it there, but the same apps paint fine on hardware
    elsewhere, so it is opt-in."""

    def _env(self, value):
        e = dict(os.environ)
        e.pop(webview2.SOFTWARE_GL_ENV, None)
        if value is not None: e[webview2.SOFTWARE_GL_ENV] = value
        return mock.patch.dict(os.environ, e, clear=True)

    def test_off_by_default(self):
        with self._env(None):
            self.assertFalse(webview2.software_gl())
            self.assertEqual(webview2.presentation_flags(), webview2.PRESENTATION_FLAGS)

    def test_the_default_never_costs_gpu_rendering(self):
        """A working hardware stack must not be switched to software GL."""
        with self._env(None):
            self.assertNotIn("swiftshader", webview2.presentation_flags())
            self.assertNotIn("--disable-gpu", webview2.presentation_flags())

    def test_opting_in_adds_the_flags_and_keeps_the_originals(self):
        with self._env("1"):
            f = webview2.presentation_flags()
            self.assertIn("--use-gl=swiftshader", f)
            self.assertIn("--enable-unsafe-swiftshader", f)
            self.assertIn("--disable-gpu", f)
            self.assertIn("--disable-direct-composition", f)
            self.assertIn("--ui-disable-partial-swap", f)

    def test_the_usual_truthy_spellings_work(self):
        for v in ("1", "true", "TRUE", "yes", "on"):
            with self._env(v):
                self.assertTrue(webview2.software_gl(), v)

    def test_anything_else_is_off(self):
        for v in ("", "0", "false", "no", "off", "maybe"):
            with self._env(v):
                self.assertFalse(webview2.software_gl(), repr(v))

    def test_the_audio_modeling_module_follows_the_same_setting(self):
        """Its FLAGS used to be a second copy of the string; a user who set the
        variable would otherwise have seen the Center ignore it."""
        from vstenv import vendors
        am = vendors.get("am")
        with self._env(None):
            self.assertEqual(am.flags(None), webview2.PRESENTATION_FLAGS)
        with self._env("1"):
            self.assertIn("swiftshader", am.flags(None))


if __name__ == "__main__":
    unittest.main()


class PrefixSettingTest(unittest.TestCase):
    """The setting lives in the prefix, so the GUI can own it; the variable
    only overrides it for one run."""

    def _prefix(self, d):
        import types
        from pathlib import Path
        return types.SimpleNamespace(drive_c=Path(d))

    def test_off_with_no_settings_file(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {}, clear=True):
            self.assertFalse(webview2.software_gl(self._prefix(d)))

    def test_on_when_the_file_says_so(self):
        import json, tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {}, clear=True):
            (Path(d) / webview2.SETTINGS).write_text(json.dumps({"software_gl": True}))
            self.assertTrue(webview2.software_gl(self._prefix(d)))
            self.assertIn("swiftshader", webview2.presentation_flags(self._prefix(d)))

    def test_set_writes_the_file_and_rewrites_every_host(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {}, clear=True):
            p = self._prefix(d); calls = []
            p.run = lambda cmd, timeout=0: (calls.append(cmd), mock.Mock(returncode=0))[1]
            with mock.patch.object(webview2, "host_exes", return_value=["A.exe", "B.exe"]):
                done = webview2.set_software_gl(p, True)
            self.assertEqual(done, ["A.exe", "B.exe"])
            self.assertTrue(webview2.software_gl(p))
            self.assertEqual(len(calls), 2)
            for c in calls: self.assertIn("swiftshader", c[c.index("/d") + 1])
            with mock.patch.object(webview2, "host_exes", return_value=["A.exe", "B.exe"]):
                webview2.set_software_gl(p, False)
            self.assertFalse(webview2.software_gl(p))
            self.assertNotIn("swiftshader", calls[-1][calls[-1].index("/d") + 1])

    def test_a_corrupt_settings_file_means_off(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {}, clear=True):
            (Path(d) / webview2.SETTINGS).write_text("{not json")
            self.assertFalse(webview2.software_gl(self._prefix(d)))


class LaunchHintTest(unittest.TestCase):
    """Health naming the variable only helps someone who thinks to open
    Health. A user staring at a blank Software Center has no reason to."""

    def setUp(self):
        from pathlib import Path
        self.src = Path("vstenv/gui.py").read_text()

    def test_launching_a_program_offers_the_hint(self):
        start = self.src.index("def run_program(self, prog)")
        self.assertIn("_webview2_hint", self.src[start:start + 900])

    def test_it_is_skipped_when_already_opted_in(self):
        start = self.src.index("def _webview2_hint")
        body = self.src[start:start + 1200]
        self.assertIn("if webview2.software_gl(self.prefix): return", body)

    def test_it_is_only_for_webview2_hosts(self):
        """A JUCE instrument launching must not be told about WebView2."""
        start = self.src.index("def _webview2_hint")
        body = self.src[start:start + 1200]
        self.assertIn("host_exes", body)

    def test_the_hint_names_the_menu_item_not_a_variable(self):
        """Setup is run from the app; a Flatpak user has nowhere to type an
        environment variable."""
        start = self.src.index("def _webview2_hint")
        body = self.src[start:start + 1400]
        self.assertIn("SOFTWARE_GL_LABEL", body)
        self.assertNotIn("SOFTWARE_GL_ENV", body)

    def test_the_menu_has_the_toggle(self):
        self.assertIn('"app.softgl"', self.src)
        self.assertIn('new_stateful("softgl"', self.src)

    def test_it_shows_once_per_session(self):
        start = self.src.index("def _webview2_hint")
        body = self.src[start:start + 1200]
        self.assertIn("_wv2_hinted", body)
