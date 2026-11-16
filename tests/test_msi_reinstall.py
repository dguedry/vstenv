"""An .msi that is already installed is removed first, so its install wizard runs
instead of a maintenance wizard whose Remove does nothing under Wine (#20)."""
import subprocess, tempfile, unittest
from pathlib import Path
from unittest import mock

from vstenv import programs
from vstenv.progress import null_reporter
from vstenv.wine import Prefix, WineBuild

CODE = "{55A22B0A-E6FF-45FA-9D30-3477BCE9D039}"


class RemoveInstalledMsi(unittest.TestCase):
    def _run(self, code, registered, exit_code=0):
        with tempfile.TemporaryDirectory() as tmp:
            p = Prefix(Path(tmp) / "prefix", WineBuild(Path(tmp) / "wine"))
            msi = Path(tmp) / "hub.msi"; msi.write_bytes(b"")
            query = lambda key: {"DisplayName": "Hub"} if registered and key.startswith("HKLM") and key.endswith(CODE) else {}
            with mock.patch.object(programs, "msi_product_code", return_value=code), \
                 mock.patch.object(p, "reg_query", side_effect=query), \
                 mock.patch.object(p, "run", return_value=subprocess.CompletedProcess([], exit_code)) as run:
                return programs._remove_installed_msi(p, msi, null_reporter(None)), run

    def test_an_installed_product_is_removed_silently(self):
        done, run = self._run(CODE, registered=True)
        self.assertTrue(done)
        self.assertEqual(run.call_args[0][0], ["msiexec", "/x", CODE, "/qn"])

    def test_a_product_not_installed_is_left_to_the_installer(self):
        done, run = self._run(CODE, registered=False)
        self.assertFalse(done); run.assert_not_called()

    def test_an_unreadable_or_odd_product_code_runs_nothing(self):
        for code in ("", "not-a-guid", CODE + " /qn"):
            done, run = self._run(code, registered=True)
            self.assertFalse(done); run.assert_not_called()

    def test_a_failed_removal_does_not_stop_the_install(self):
        done, _ = self._run(CODE, registered=True, exit_code=1603)
        self.assertFalse(done)

    def test_a_file_that_is_not_an_msi_has_no_product_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "x.msi"; f.write_bytes(b"MZ")
            self.assertEqual(programs.msi_product_code(f), "")


if __name__ == "__main__":
    unittest.main()
