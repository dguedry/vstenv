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


class AfterChangeSkipTest(unittest.TestCase):
    """The after-install epilogue is skipped when nothing it reacts to changed:
    a manager opened and closed without installing used to pay the full pass."""
    def test_second_pass_skipped_until_something_changes(self):
        import os, tempfile, time
        from unittest import mock
        from pathlib import Path
        from vstenv import setup
        from vstenv.wine import Prefix, WineBuild
        with tempfile.TemporaryDirectory() as tmp:
            p = Prefix(Path(tmp) / "prefix", WineBuild(Path(tmp) / "wine"))
            d = p.drive_c / "Program Files/Some Vendor"; d.mkdir(parents=True)
            (p.drive_c / "users/me").mkdir(parents=True)
            sig = Path(tmp) / "after-change.sig"
            with mock.patch.object(setup, "CHANGE_SIG", sig), \
                 mock.patch.object(setup.vendors, "all", return_value=[]), \
                 mock.patch.object(setup.dcomp, "apply_program_overrides"), \
                 mock.patch.object(setup.dcomp, "program_exes", return_value=[]), \
                 mock.patch.object(setup.dcomp, "all_plugin_overrides", return_value=[]), \
                 mock.patch.object(setup.webview2, "needed", return_value=False), \
                 mock.patch.object(setup.webview2, "apply_presentation_flags"), \
                 mock.patch.object(setup.yabridge, "write_plugin_overrides"), \
                 mock.patch.object(setup.yabridge, "sync", return_value={"ok": True}) as ysync, \
                 mock.patch.object(setup.menu, "sync"):
                self.assertEqual(setup.after_change(p), {"ok": True})     # first pass runs
                self.assertEqual(setup.after_change(p), {"skipped": True})  # nothing changed
                exe = d / "new.exe"; exe.write_bytes(b"MZ")
                os.utime(d, (time.time() + 5, time.time() + 5))
                self.assertEqual(setup.after_change(p), {"ok": True})     # a change runs it again
                self.assertEqual(setup.after_change(p, force=True), {"ok": True})  # force always runs
                self.assertEqual(ysync.call_count, 3)
