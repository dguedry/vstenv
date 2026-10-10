import unittest
from pathlib import Path
from unittest import mock

from vstenv import vendors


class PendingManagerUpdateTest(unittest.TestCase):
    """Native Access's self-updater is disabled by this app, so NA never acts
    on an update it downloaded before that. The file can sit there forever;
    this is the way to install it, patched."""

    def test_every_vendor_answers_the_hook(self):
        """Vendors with no self-updating manager answer None without touching
        the prefix; NI implements it for real and is covered below."""
        p = mock.Mock()
        for v in vendors.all():
            if v.id == "ni": continue
            self.assertIsNone(v.pending_manager_update(p), v.id)

    def test_a_vendor_without_a_manager_applies_nothing(self):
        am = vendors.get("am")
        self.assertFalse(am.apply_pending_manager_update(mock.Mock()))

    def test_ni_reports_the_file_native_access_left(self):
        ni = vendors.get("ni")
        from vstenv.vendors.ni import native_access as na
        with mock.patch.object(na, "pending_update", return_value=Path("/x/Native-Access-latest.exe")):
            self.assertEqual(ni.pending_manager_update(mock.Mock()),
                             Path("/x/Native-Access-latest.exe"))

    def test_ni_applies_it_through_this_app(self):
        """It must go through install_native_access, which re-applies the fixes
        -- installing it any other way would leave NA unpatched."""
        ni = vendors.get("ni")
        from vstenv.vendors.ni import native_access as na
        with mock.patch.object(na, "apply_pending_update", return_value=True) as applied:
            self.assertTrue(ni.apply_pending_manager_update(mock.Mock()))
        applied.assert_called_once()

    def test_nothing_pending_is_not_an_error(self):
        ni = vendors.get("ni")
        from vstenv.vendors.ni import native_access as na
        with mock.patch.object(na, "pending_update", return_value=None):
            self.assertIsNone(ni.pending_manager_update(mock.Mock()))


class SelfUpdateStaysDisabledTest(unittest.TestCase):
    """The design: NA does not self-update, because an unvalidated version can
    hit a patch site that moved and leave NA unpatchable. Applying a pending
    update must not re-enable the updater."""

    def test_applying_an_update_goes_through_the_patching_installer(self):
        from vstenv.vendors.ni import native_access as na
        import inspect
        src = inspect.getsource(na.apply_pending_update)
        self.assertIn("install_native_access", src,
                      "a pending update must be installed by the path that patches it")

    def test_the_patching_installer_disables_self_update_again(self):
        from vstenv.vendors.ni import native_access as na
        import inspect
        src = inspect.getsource(na.install_native_access)
        self.assertIn("disable_self_update", src)


if __name__ == "__main__":
    unittest.main()
