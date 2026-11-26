"""shim: ~/.local/bin/vstenv for the Flatpak's command line, never over someone else's file."""
import tempfile, unittest
from pathlib import Path
from unittest import mock
from vstenv import shim, menu

class ShimTest(unittest.TestCase):
    def _at(self, d):
        b = Path(d) / "bin"
        return mock.patch.object(shim, "BIN", b), mock.patch.object(shim, "PATH", b / "vstenv"), b / "vstenv"

    def test_written_only_from_the_flatpak_and_runs_its_command_line(self):
        with tempfile.TemporaryDirectory() as d:
            pb, pp, f = self._at(d)
            with pb, pp, mock.patch.object(shim.host, "sh", return_value=str(f.parent)):
                with mock.patch.object(shim.host, "in_flatpak", return_value=False):
                    self.assertFalse(shim.install()); self.assertFalse(f.exists())
                with mock.patch.object(shim.host, "in_flatpak", return_value=True):
                    self.assertTrue(shim.install()); self.assertTrue(shim.install())      # idempotent
                self.assertIn('exec flatpak run --command=vstenv io.github.dguedry.vstenv "$@"', f.read_text())
                self.assertTrue(f.stat().st_mode & 0o100); self.assertTrue(shim.ours())
                self.assertTrue(shim.remove()); self.assertFalse(f.exists()); self.assertFalse(shim.remove())

    def test_a_file_that_is_not_ours_is_left_alone(self):
        with tempfile.TemporaryDirectory() as d:
            pb, pp, f = self._at(d); f.parent.mkdir(); f.write_text("#!/bin/sh\nexec python3 -m vstenv \"$@\"\n")
            with pb, pp, mock.patch.object(shim.host, "in_flatpak", return_value=True):
                self.assertFalse(shim.install()); self.assertFalse(shim.remove())
            self.assertIn("python3 -m vstenv", f.read_text())

    def test_says_so_when_local_bin_is_not_on_the_path(self):
        with tempfile.TemporaryDirectory() as d:
            pb, pp, f = self._at(d); r = mock.Mock()
            with pb, pp, mock.patch.object(shim.host, "in_flatpak", return_value=True), mock.patch.object(shim.host, "sh", return_value="/usr/bin:/bin"), \
                 mock.patch.object(shim, "null_reporter", return_value=r):
                shim.install()
            self.assertIn("not on your PATH", r.ok.call_args.args[0])

    def test_desktop_entries_do_not_go_through_the_shim(self):
        with tempfile.TemporaryDirectory() as d:
            pb, pp, f = self._at(d); f.parent.mkdir(); f.write_text(shim.content())
            ok = mock.Mock(returncode=0)
            with pb, pp, mock.patch.object(menu.host, "in_flatpak", return_value=False), mock.patch.object(menu.host, "which", side_effect=lambda n: str(f) if n == "vstenv" else "/usr/bin/flatpak"), \
                 mock.patch("subprocess.run", return_value=ok):
                self.assertEqual(menu.launcher(), "flatpak run --command=vstenv io.github.dguedry.vstenv")
            with mock.patch.object(menu.host, "in_flatpak", return_value=False), mock.patch.object(menu.host, "which", return_value="/usr/bin/vstenv"):
                self.assertEqual(menu.launcher(), "vstenv")          # a real command of that name still wins

if __name__ == "__main__": unittest.main()
