"""The first launch after an app update runs prepare() once, so existing users
pick up new fixes (patched DLLs, launcher blocks, registry keys) without knowing
to press repair."""
import tempfile, unittest
from pathlib import Path
from unittest import mock
from vstenv import setup


class UpgradePassTest(unittest.TestCase):
    def test_needs_pass_when_stamp_missing_or_stale_and_not_after_marking(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(setup, "STAMP", Path(tmp) / "last-run-version"):
                self.assertTrue(setup.needs_upgrade_pass())          # never ran
                setup.STAMP.write_text("0.0.1")
                self.assertTrue(setup.needs_upgrade_pass())          # older version ran
                setup.mark_version_ran()
                self.assertFalse(setup.needs_upgrade_pass())         # current version ran

    def test_upgrade_pass_runs_prepare_then_stamps(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(setup, "STAMP", Path(tmp) / "last-run-version"), \
                 mock.patch.object(setup, "prepare") as prep:
                setup.upgrade_pass(object())
                prep.assert_called_once()
                self.assertFalse(setup.needs_upgrade_pass())


if __name__ == "__main__": unittest.main()
