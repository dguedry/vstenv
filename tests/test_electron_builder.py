import types
import unittest
from pathlib import Path
from unittest import mock

from vstenv import programs

LISTING = """
   Date      Time    Attr         Size   Compressed  Name
------------------- ----- ------------ ------------  ------------------------
                    .....         6144               $PLUGINSDIR/nsExec.dll
                    .....       107520               $PLUGINSDIR/nsis7z.dll
                    .....    259000000               $PLUGINSDIR/app-64.7z
"""


class ElectronBuilderTest(unittest.TestCase):
    """Softube Central 3.0.0 (2026-10-30): Wine's powershell.exe stub exits 0 for
    anything, and electron-builder's app-running check reads 0 as "running", so
    the setup said "Softube Central cannot be closed" with nothing running."""

    def _ls(self, out):
        return mock.patch("subprocess.run", return_value=types.SimpleNamespace(stdout=out))

    def test_detects_an_electron_builder_setup(self):
        with self._ls(LISTING), mock.patch("vstenv.tools.seven_zip", return_value="7zz"):
            self.assertTrue(programs.is_electron_builder(Path("Softube Central Setup 3.0.0.exe")))

    def test_a_plain_nsis_setup_is_not(self):
        with self._ls("$PLUGINSDIR/nsExec.dll\n$PLUGINSDIR/System.dll\n"), mock.patch("vstenv.tools.seven_zip", return_value="7zz"):
            self.assertFalse(programs.is_electron_builder(Path("Helper Installer.exe")))

    def test_msi_is_never_checked(self):
        self.assertFalse(programs.is_electron_builder(Path("x.msi")))

    def test_install_disables_the_stub_only_for_those(self):
        src = Path("vstenv/programs.py").read_text()
        self.assertIn('";powershell.exe=d"', src)
        self.assertIn("if is_electron_builder(installer):", src)
