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


class BundledRuntimeDetectionTest(unittest.TestCase):
    """Second line of defence: a program shipping its own Chromium runtime is
    drawing through that browser, whatever its imports say, so it is found
    without anyone naming it."""

    def _prefix(self, tmp):
        from pathlib import Path
        p = mock.Mock()
        p.drive_c = Path(tmp)
        return p

    def test_a_program_bundling_a_runtime_is_found(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            app = Path(tmp) / "Program Files" / "Some Player"
            (app / "BrowserRuntime").mkdir(parents=True)
            (app / "Some Player.exe").write_bytes(b"MZ")
            (app / "BrowserRuntime" / "msedgewebview2.exe").write_bytes(b"MZ")
            found = dcomp._bundled_runtime_exes(self._prefix(tmp))
        self.assertEqual(found, {"Some Player.exe"})

    def test_a_program_without_a_runtime_is_not_found(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            app = Path(tmp) / "Program Files" / "Some Instrument"
            app.mkdir(parents=True)
            (app / "Some Instrument.exe").write_bytes(b"MZ")
            self.assertEqual(dcomp._bundled_runtime_exes(self._prefix(tmp)), set())

    def test_an_edge_install_is_not_a_program_bundling_a_runtime(self):
        """The runtime has to sit BELOW the program, not be the program."""
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            edge = Path(tmp) / "Program Files" / "Microsoft" / "EdgeWebView"
            edge.mkdir(parents=True)
            (edge / "msedgewebview2.exe").write_bytes(b"MZ")
            self.assertEqual(dcomp._bundled_runtime_exes(self._prefix(tmp)), set())

    def test_the_runtimes_own_helpers_are_not_reported(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            app = Path(tmp) / "Program Files" / "Player"
            rt = app / "BrowserRuntime"
            rt.mkdir(parents=True)
            (app / "Player.exe").write_bytes(b"MZ")
            for helper in ("msedgewebview2.exe", "notification_helper.exe", "mscopilot.exe"):
                (rt / helper).write_bytes(b"MZ")
            self.assertEqual(dcomp._bundled_runtime_exes(self._prefix(tmp)), {"Player.exe"})

    def test_a_missing_prefix_is_not_an_error(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(dcomp._bundled_runtime_exes(self._prefix(tmp)), set())


class PluginOverrideTest(unittest.TestCase):
    """The plugin path needs the same exclusion as the program path: SINE ships
    both an exe and a VST3, and excluding only the exe left its plugin editor
    with the same broken tabs."""

    def test_a_browser_drawn_plugin_is_excluded(self):
        from pathlib import Path
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "SINE Player.vst3").mkdir()
            (d / "BBC Symphony Orchestra (64 Bit).vst3").mkdir()
            for name in ("SINE Player.vst3", "BBC Symphony Orchestra (64 Bit).vst3"):
                (d / name / "plugin.dll").write_bytes(b"MZ")
            p = mock.Mock(); p.drive_c = d
            with mock.patch.object(dcomp, "browser_window_exes", return_value={"SINE Player.exe"}), \
                 mock.patch.object(dcomp, "imports_dcomp", return_value=True), \
                 mock.patch("vstenv.yabridge.plugin_dirs", return_value=[d]), \
                 mock.patch.object(dcomp, "_load", return_value={}), \
                 mock.patch.object(dcomp, "_save"):
                names = [Path(b).name for b, _ in dcomp.plugin_overrides(p)]
        self.assertNotIn("SINE Player.vst3", names)
        self.assertIn("BBC Symphony Orchestra (64 Bit).vst3", names)
