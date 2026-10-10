import unittest
from pathlib import Path
from unittest import mock

from vstenv import vendors, dcomp
from vstenv.vendors.izotope import MANAGER_EXE, LICENCE_KEY


class IZotopeVendorTest(unittest.TestCase):
    def setUp(self):
        self.v = vendors.get("izotope")

    def test_registered_as_a_builtin(self):
        self.assertIn("izotope", [v.id for v in vendors.all()])

    def test_recognises_the_portal_installer(self):
        for name in ("iZotope_Product_Portal_v1_4_9.exe", "Product Portal Installer.exe"):
            self.assertTrue(self.v.accepts_manager_installer(name), name)

    def test_does_not_claim_a_plugin_installer_or_another_vendor(self):
        for other in ("iZotope_Ozone_11_Advanced.exe", "Native-Access-2.exe", "ik_product_manager.exe"):
            self.assertFalse(self.v.accepts_manager_installer(other), other)

    def test_only_one_vendor_claims_the_portal_installer(self):
        name = "iZotope_Product_Portal_v1_4_9.exe"
        self.assertEqual([v.id for v in vendors.all() if v.accepts_manager_installer(name)], ["izotope"])

    def test_the_portal_keeps_dxvk(self):
        """Its window is Chromium's; the composition patches would break it the
        way they broke SINE Player."""
        self.assertIn(MANAGER_EXE, dcomp.browser_window_exes())


class LicenceStateTest(unittest.TestCase):
    """iZotope records authorisation in one flat account key, not per product,
    so the module reports what that key says and does not invent per-plugin
    licence claims."""

    def _prefix(self, vals):
        p = mock.Mock(); p.reg_query.return_value = vals; return p

    def test_an_activation_pair_counts_as_authorised(self):
        v = vendors.get("izotope")
        self.assertTrue(v._authorised(self._prefix({"Challenge": "IZ-A", "Response": "IZ-B"})))

    def test_the_analytics_auth_state_counts(self):
        v = vendors.get("izotope")
        self.assertTrue(v._authorised(self._prefix({"LastSentAnalyticsAuthState": "Authorized"})))

    def test_nothing_recorded_is_unknown_not_unlicensed(self):
        v = vendors.get("izotope")
        self.assertIsNone(v._authorised(self._prefix({})))
        self.assertIsNone(v._authorised(self._prefix({"EnableAnalytics": "1"})))

    def test_an_unreadable_registry_is_not_fatal(self):
        v = vendors.get("izotope")
        p = mock.Mock(); p.reg_query.side_effect = OSError("no prefix")
        self.assertIsNone(v._authorised(p))

    def test_products_exclude_the_manager_itself(self):
        v = vendors.get("izotope")
        progs = [mock.Mock(name_="Product Portal", version="", install_dir="C:/x", publisher="iZotope, Inc."),
                 mock.Mock(name_="Ozone 9 Advanced", version="9.13.0", install_dir="C:/y", publisher="iZotope, Inc."),
                 mock.Mock(name_="Kontakt 8", version="8.1", install_dir="C:/z", publisher="Native Instruments")]
        for m, n in zip(progs, ("Product Portal", "Ozone 9 Advanced", "Kontakt 8")): m.name = n
        p = mock.Mock(); p.reg_query.return_value = {}
        with mock.patch("vstenv.programs.installed", return_value=progs):
            names = [x.name for x in v.products(p)]
        self.assertEqual(names, ["Ozone 9 Advanced"])


if __name__ == "__main__":
    unittest.main()
