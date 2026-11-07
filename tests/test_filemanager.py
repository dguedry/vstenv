import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from vstenv import filemanager as fm


class FileManagerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); share = Path(self.tmp.name)
        self.patches = [mock.patch.object(fm, "SHARE", share),
                        mock.patch.object(fm, "MIME_PKG", share / "mime/packages/x.xml"),
                        mock.patch.object(fm, "DOLPHIN", share / "kio/servicemenus/x.desktop"),
                        mock.patch.object(fm, "NEMO", share / "nemo/actions/x.nemo_action"),
                        mock.patch.object(fm, "NAUTILUS", share / "nautilus/scripts" / fm.NAME),
                        mock.patch.object(fm, "gui_launcher", return_value="flatpak run io.github.dguedry.vstenv"),
                        mock.patch.object(fm.host, "desktop_tool", return_value=None)]
        for p in self.patches: p.start()
    def tearDown(self):
        for p in self.patches: p.stop()
        self.tmp.cleanup()

    def test_writes_each_entry_and_removes_them(self):
        self.assertGreater(fm.apply(), 0)
        d = fm.DOLPHIN.read_text()
        self.assertIn("Exec=flatpak run io.github.dguedry.vstenv %f", d); self.assertIn("application/zip;", d)
        self.assertIn("application/x-vst3-plugin", d)
        self.assertTrue(os.access(fm.DOLPHIN, os.X_OK)); self.assertTrue(os.access(fm.NAUTILUS, os.X_OK))
        self.assertIn("Extensions=exe;msi;zip;", fm.NEMO.read_text())
        self.assertIn('exec flatpak run io.github.dguedry.vstenv "$1"', fm.NAUTILUS.read_text())
        self.assertIn("*.clap", fm.MIME_PKG.read_text())
        self.assertEqual(fm.apply(), 0)                 # nothing changed the second time
        self.assertEqual(fm.remove(), 4)
        self.assertFalse(any(Path(p).exists() for p in fm.status()))

    def test_leaves_a_users_own_file_alone(self):
        fm.NEMO.parent.mkdir(parents=True); fm.NEMO.write_text("[Nemo Action]\nName=mine\n")
        fm.apply(); fm.remove()
        self.assertEqual(fm.NEMO.read_text(), "[Nemo Action]\nName=mine\n")



class LauncherTest(unittest.TestCase):
    def test_flatpak_opens_the_window(self):
        with mock.patch.object(fm.menu, "launcher", return_value="flatpak run --command=vstenv io.github.dguedry.vstenv"):
            self.assertEqual(fm.gui_launcher(), "flatpak run io.github.dguedry.vstenv")
    def test_source_checkout_runs_the_gui_module(self):
        with mock.patch.object(fm.menu, "launcher", return_value="env PYTHONPATH=/src /usr/bin/python3 -m vstenv"):
            self.assertEqual(fm.gui_launcher(), "env PYTHONPATH=/src /usr/bin/python3 -m vstenv.gui")

if __name__ == "__main__": unittest.main()
