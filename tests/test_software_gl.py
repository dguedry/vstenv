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
            self.assertEqual(am.FLAGS, webview2.PRESENTATION_FLAGS)
        with self._env("1"):
            self.assertIn("swiftshader", am.FLAGS)


if __name__ == "__main__":
    unittest.main()
