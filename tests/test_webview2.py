"""On-demand WebView2 runtime: detect apps that host embedded Edge, install the
runtime only when one is present, and don't flag the runtime's own folders."""
import tempfile, unittest
from pathlib import Path
from unittest import mock
from vstenv import webview2
from vstenv.wine import Prefix, WineBuild

import tempfile as _tf
webview2._SCAN_CACHE = Path(_tf.mkdtemp(prefix="wv2cache")) / "scan.json"   # keep test scans out of the user's data dir


class WebView2DetectTest(unittest.TestCase):
    def _prefix(self, tmp):
        p = Prefix(Path(tmp) / "prefix", WineBuild(Path(tmp) / "wine"))
        (p.drive_c).mkdir(parents=True, exist_ok=True)
        return p

    def _exe(self, path: Path, marker=b"CoreWebView2"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"MZ" + b"\0" * 64 + marker)

    def test_finds_app_with_bundled_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = self._prefix(tmp)
            self._exe(p.drive_c / "Program Files/SINE Player/SINE Player.exe", b"plain")
            (p.drive_c / "Program Files/SINE Player/BrowserRuntime").mkdir(parents=True)
            self._exe(p.drive_c / "Program Files/SINE Player/BrowserRuntime/msedgewebview2.exe")
            self.assertIn("SINE Player", webview2.apps_present(p))

    def test_finds_app_by_exe_marker_even_if_name_differs(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = self._prefix(tmp)
            self._exe(p.drive_c / "Program Files/Audio Modeling/Software Center/Audio Modeling Software Center.exe")
            self.assertIn("Software Center", webview2.apps_present(p))

    def test_ignores_the_runtimes_own_folders(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = self._prefix(tmp)
            self._exe(p.drive_c / "Program Files (x86)/Microsoft/EdgeWebView/Application/154.0/msedgewebview2.exe")
            self.assertEqual(webview2.apps_present(p), [])          # the runtime is not an "app"

    def test_plain_app_not_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = self._prefix(tmp)
            self._exe(p.drive_c / "Program Files/FabFilter/Pro-Q 4/FFProQ4.exe", b"just a plugin")
            self.assertEqual(webview2.apps_present(p), [])

    def test_needed_only_when_app_present_and_no_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = self._prefix(tmp)
            self._exe(p.drive_c / "Program Files/SINE Player/SINE Player.exe")
            with mock.patch.object(webview2, "installed", return_value=False):
                self.assertTrue(webview2.needed(p))
            with mock.patch.object(webview2, "installed", return_value=True):
                self.assertFalse(webview2.needed(p))     # runtime present -> not needed

    def test_installed_reads_the_client_registry_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = self._prefix(tmp)
            with mock.patch.object(p, "reg_query", return_value={"pv": "154.0.4258.48"}):
                self.assertTrue(webview2.installed(p))
            with mock.patch.object(p, "reg_query", return_value={}):
                self.assertFalse(webview2.installed(p))


if __name__ == "__main__": unittest.main()


class PresentationFlagsTest(unittest.TestCase):
    """Every WebView2 host gets the full-frame presentation flags through the
    policy key, but a value someone (or a vendor module) already set stays."""
    def test_flags_set_for_hosts_without_a_value(self):
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            p = Prefix(Path(tmp) / "prefix", WineBuild(Path(tmp) / "wine"))
            exe = p.drive_c / "Program Files/SINE Player/SINE Player.exe"
            exe.parent.mkdir(parents=True); exe.write_bytes(b"MZ" + b"CoreWebView2")
            other = p.drive_c / "Program Files/Audio Modeling/Software Center/Audio Modeling Software Center.exe"
            other.parent.mkdir(parents=True); other.write_bytes(b"MZ" + b"WebView2Loader")
            with mock.patch.object(p, "reg_query", return_value={"Audio Modeling Software Center.exe": "--disable-gpu"}), \
                 mock.patch.object(p, "run", return_value=subprocess.CompletedProcess([], 0)) as run:
                n = webview2.apply_presentation_flags(p)
            self.assertEqual(n, 1)                       # only the host with no value
            args = run.call_args[0][0]
            self.assertIn("SINE Player.exe", args)
            self.assertIn(webview2.PRESENTATION_FLAGS, args)

    def test_bundled_runtime_app_counts_even_without_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Prefix(Path(tmp) / "prefix", WineBuild(Path(tmp) / "wine"))
            d = p.drive_c / "Program Files/SINE Player"
            (d / "BrowserRuntime").mkdir(parents=True)
            (d / "BrowserRuntime/msedgewebview2.exe").write_bytes(b"MZ")
            (d / "SINE Player.exe").write_bytes(b"MZ plain")
            (d / "unins000.exe").write_bytes(b"MZ plain")
            self.assertEqual(webview2.host_exes(p), ["SINE Player.exe"])

    def test_webview2_plugin_pulls_in_the_yabridge_hosts(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Prefix(Path(tmp) / "prefix", WineBuild(Path(tmp) / "wine"))
            mod = p.drive_c / "Program Files/Common Files/VST3/SINE Player.vst3/Contents/x86_64-win/SINE Player.vst3"
            mod.parent.mkdir(parents=True); mod.write_bytes(b"MZ" + b"WebView2Loader")
            self.assertIn("yabridge-host.exe", webview2.host_exes(p))
            self.assertIn("yabridge-host-32.exe", webview2.host_exes(p))

    def test_marker_scan_is_cached_by_size_and_mtime(self):
        import os, time
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / "a.exe"; exe.write_bytes(b"MZ CoreWebView2")
            self.assertTrue(webview2._exe_uses_webview2(exe))
            with mock.patch.object(Path, "read_bytes", side_effect=AssertionError("re-read of an unchanged file")):
                self.assertTrue(webview2._exe_uses_webview2(exe))      # cache hit
            exe.write_bytes(b"MZ plain"); os.utime(exe, (time.time() + 5, time.time() + 5))
            self.assertFalse(webview2._exe_uses_webview2(exe))         # changed file is re-read

    def test_tauri2_host_is_found(self):
        # wry + webview2-com 0.38 (Mercurial Tones Hub): no WebView2Loader, and
        # "CoreWebView2" only in UTF-16.
        with tempfile.TemporaryDirectory() as tmp:
            for body in (b"MZ" + "CoreWebView2".encode("utf-16-le"), b"MZ webview2-com-0.38.2"):
                exe = Path(tmp) / f"hub{len(body)}.exe"; exe.write_bytes(body)
                self.assertTrue(webview2._exe_uses_webview2(exe))

    def test_tauri2_host_gets_only_full_frame_presentation(self):
        # DirectComposition off made the Mercurial Tones Hub's clicks miss after
        # its window was moved; a host the older markers find keeps both flags.
        with tempfile.TemporaryDirectory() as tmp:
            p = Prefix(Path(tmp) / "prefix", WineBuild(Path(tmp) / "wine"))
            hub = p.drive_c / "Program Files/Mercurial Tones Hub/mercurial-software-center.exe"
            hub.parent.mkdir(parents=True); hub.write_bytes(b"MZ webview2-com-0.38.2")
            old = p.drive_c / "Program Files/Audio Modeling/Software Center/Software Center.exe"
            old.parent.mkdir(parents=True); old.write_bytes(b"MZ CoreWebView2 webview2-com")
            self.assertEqual(webview2.host_exes(p), ["Software Center.exe", "mercurial-software-center.exe"])
            with mock.patch.object(webview2, "software_gl", return_value=False):
                self.assertEqual(webview2.presentation_flags(p, "mercurial-software-center.exe"), webview2.TAURI2_FLAGS)
                self.assertEqual(webview2.presentation_flags(p, "Software Center.exe"), webview2.PRESENTATION_FLAGS)
                self.assertEqual(webview2.presentation_flags(p), webview2.PRESENTATION_FLAGS)

    def test_cached_miss_from_older_markers_is_read_again(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / "hub.exe"; exe.write_bytes(b"MZ webview2-com")
            st = exe.stat()
            webview2._load_cache()[str(exe)] = [st.st_size, st.st_mtime, False]   # written by an older version
            self.assertTrue(webview2._exe_uses_webview2(exe))
