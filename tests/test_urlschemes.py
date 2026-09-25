"""A vendor's manager may sign in through the browser, which calls back to its
own scheme (native-access://). Without a handler registered with the desktop the
login never returns; these cover installing and removing that handler."""
import tempfile, unittest
from pathlib import Path
from unittest import mock
from vstenv import urlschemes, vendors
from vstenv.wine import Prefix, WineBuild

class UrlSchemesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); root = Path(self.tmp.name)
        (root / "wine/bin").mkdir(parents=True)
        (root / "wine/bin/wine").write_text("#!/bin/sh\n"); (root / "wine/bin/wine").chmod(0o755)
        self.p = Prefix(root / "prefix", WineBuild(root / "wine"))
        (self.p.drive_c / "Program Files/Native Instruments/Native Access").mkdir(parents=True)
        self.scheme = vendors.get("ni").url_schemes(self.p)[0]
        self.patches = [mock.patch.object(urlschemes, "BIN", root / ".local/bin"),
                        mock.patch.object(urlschemes, "APPS", root / ".local/share/applications"),
                        mock.patch.object(urlschemes.shutil, "which", return_value=None),
                        mock.patch.object(urlschemes.subprocess, "run", return_value=mock.Mock(stdout="", returncode=0))]
        for x in self.patches: x.start()
        self.script, self.desktop = urlschemes.script_path(self.scheme), urlschemes.desktop_path(self.scheme)

    def tearDown(self):
        for x in self.patches: x.stop()
        self.tmp.cleanup()

    def test_the_ni_module_declares_native_access(self):
        self.assertEqual(self.scheme.scheme, "native-access")

    def test_register_writes_an_executable_handler_and_a_desktop_file(self):
        self.assertTrue(urlschemes.register(self.p, self.scheme))
        self.assertTrue(self.script.exists() and self.desktop.exists())
        self.assertTrue(self.script.stat().st_mode & 0o111, "handler must be executable")
        body = self.script.read_text()
        self.assertIn(str(self.p.path), body); self.assertIn("Native Access.exe", body); self.assertIn('"$1"', body)
        self.assertIn("x-scheme-handler/native-access", self.desktop.read_text())

    def test_register_is_idempotent(self):
        urlschemes.register(self.p, self.scheme); first = self.script.read_text()
        urlschemes.register(self.p, self.scheme)
        self.assertEqual(self.script.read_text(), first)

    def test_status_reports_a_foreign_handler(self):
        urlschemes.register(self.p, self.scheme)
        with mock.patch.object(urlschemes.subprocess, "run", return_value=mock.Mock(stdout="someone-elses.desktop\n", returncode=0)):
            st = urlschemes.status(self.scheme)
            self.assertTrue(st["installed"]); self.assertTrue(st["foreign"]); self.assertFalse(st["ok"])

    def test_unregister_removes_only_our_files(self):
        urlschemes.register(self.p, self.scheme)
        self.assertTrue(urlschemes.unregister(self.scheme))
        self.assertFalse(self.script.exists()); self.assertFalse(self.desktop.exists())
        self.assertFalse(urlschemes.unregister(self.scheme))
        self.script.parent.mkdir(parents=True, exist_ok=True); self.script.write_text("#!/bin/sh\n# someone else's script\n")
        urlschemes.unregister(self.scheme)
        self.assertTrue(self.script.exists(), "must not delete a script that is not ours")

if __name__ == "__main__": unittest.main()
