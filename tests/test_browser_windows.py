import unittest
from unittest import mock

from vstenv import dcomp, vendors


class BrowserWindowTest(unittest.TestCase):
    """A program whose whole window is a WebView2 control must NOT get the
    composition-swapchain patches: the browser draws its content and wants
    DXVK. Reported by a user whose SINE Player Store and My Licenses tabs went
    blank after the patches shipped, and reproduced here."""

    def test_sine_player_is_excluded(self):
        self.assertIn("SINE Player.exe", dcomp.browser_window_exes())

    def test_the_audio_modeling_center_is_excluded_by_its_vendor(self):
        am = vendors.get("am")
        self.assertIn("Audio Modeling Software Center.exe", am.browser_window_exes())
        self.assertIn("Audio Modeling Software Center.exe", dcomp.browser_window_exes())

    def test_swam_instruments_are_not_excluded(self):
        """They bundle WebView2 support and carry the same CoreWebView2 strings
        and imports as a real WebView2 app, but they draw their own JUCE
        windows and need the patches."""
        browser = dcomp.browser_window_exes()
        for swam in ("SWAM Violin 3.exe", "SWAM Trumpet.exe", "SWAM Viola 3.exe"):
            self.assertNotIn(swam, browser, swam)

    def test_programs_no_vendor_claims_keep_the_patches(self):
        """Spitfire and HALion are the reason the patches exist; an unknown
        program must keep them rather than be excluded by guesswork."""
        browser = dcomp.browser_window_exes()
        for keep in ("Spitfire Audio.exe", "HALion Sonic.exe"):
            self.assertNotIn(keep, browser, keep)

    def test_every_vendor_can_declare_without_implementing(self):
        for v in vendors.all():
            self.assertIsInstance(v.browser_window_exes(), tuple)

    def test_a_vendor_that_raises_does_not_break_the_scan(self):
        bad = mock.Mock()
        bad.browser_window_exes.side_effect = RuntimeError("boom")
        with mock.patch.object(vendors, "all", return_value=[bad]):
            self.assertIn("SINE Player.exe", dcomp.browser_window_exes())


if __name__ == "__main__":
    unittest.main()
