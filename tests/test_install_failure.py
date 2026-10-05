import unittest
from pathlib import Path
from unittest import mock

from vstenv import programs
from vstenv.programs import InstallCancelled, InstallFailed


class InstallFailureTest(unittest.TestCase):
    """A failed installer must stop the pass. Reported by the user: a Softube
    install failed and the app carried on adjusting the environment -- quirks,
    bridging, menu entries -- which reads as if the install had worked."""

    def _prefix(self, returncode):
        p = mock.Mock()
        p.run.return_value = mock.Mock(returncode=returncode)
        p.processes.return_value = []
        return p

    def _install(self, returncode, tmpfile):
        p = self._prefix(returncode)
        with mock.patch.object(programs, "installed", return_value=[]):
            return p, programs.install(p, tmpfile)

    def setUp(self):
        import tempfile
        self.tmp = tempfile.NamedTemporaryFile(suffix=".exe", delete=False)
        self.tmp.write(b"MZ"); self.tmp.close()
        self.path = Path(self.tmp.name)

    def tearDown(self):
        self.path.unlink(missing_ok=True)

    def test_a_failed_install_raises_rather_than_continuing(self):
        with self.assertRaises(InstallFailed):
            self._install(1, self.path)

    def test_the_error_says_nothing_was_changed(self):
        with self.assertRaises(InstallFailed) as e:
            self._install(1, self.path)
        self.assertIn("nothing was changed", str(e.exception))

    def test_a_cancelled_install_is_distinguished_from_a_failure(self):
        """1602 is the user closing the installer; not a red error."""
        with self.assertRaises(InstallCancelled):
            self._install(1602, self.path)

    def test_cancelled_is_a_kind_of_failed_so_callers_can_catch_one(self):
        self.assertTrue(issubclass(InstallCancelled, InstallFailed))

    def test_reboot_required_is_a_success(self):
        """3010 means it installed and wants a reboot, which a prefix does not."""
        p, rc = self._install(3010, self.path)
        self.assertEqual(rc, 3010)

    def test_a_successful_install_still_returns_zero(self):
        p, rc = self._install(0, self.path)
        self.assertEqual(rc, 0)

    def test_the_steps_after_the_run_are_skipped_on_failure(self):
        """The proof: nothing is scanned or adjusted after a failure."""
        p = self._prefix(1)
        with mock.patch.object(programs, "installed", return_value=[]) as inst, \
             mock.patch.object(programs, "wait_for_installer") as waited:
            with self.assertRaises(InstallFailed):
                programs.install(p, self.path)
            waited.assert_not_called()
        # installed() is called once up front for the "before" set, never again
        self.assertEqual(inst.call_count, 1)


if __name__ == "__main__":
    unittest.main()
