import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from vstenv import vendors
from vstenv.vendors.arturia import INSTALLED_LOG, MANAGER_EXE


class ArturiaVendorTest(unittest.TestCase):
    def setUp(self):
        self.v = vendors.get("arturia")

    def test_registered_as_a_builtin(self):
        self.assertIn("arturia", [v.id for v in vendors.all()])

    def test_recognises_its_installer(self):
        for n in ("Arturia Software Center Setup.exe", "ASC_Setup_2.12.0.exe"):
            self.assertTrue(self.v.accepts_manager_installer(n), n)

    def test_does_not_claim_an_instrument_or_another_vendor(self):
        for n in ("Analog Lab V Setup.exe", "Native-Access-2.exe", "iZotope_Product_Portal.exe"):
            self.assertFalse(self.v.accepts_manager_installer(n), n)

    def test_only_one_vendor_claims_its_installer(self):
        n = "Arturia Software Center Setup.exe"
        self.assertEqual([v.id for v in vendors.all() if v.accepts_manager_installer(n)], ["arturia"])

    def test_the_software_center_runs_through_the_app(self):
        """Its installer refuses to replace files a loaded plugin has mapped,
        so it must not get a plain desktop shortcut."""
        from vstenv import programs
        prog = programs.Program(name="Arturia Software Center 2.12.0", publisher="",
                                exe=r"C:\x\Arturia Software Center.exe", install_dir=r"C:\x")
        self.assertTrue(vendors.run_through_app(prog))


class InstalledLogTest(unittest.TestCase):
    """ASC records what it installed, with versions, in its own log. The
    registry holds only install directories, and scanner.json is a detection
    ruleset rather than a record of anything owned."""

    def _prefix(self, payload):
        tmp = tempfile.mkdtemp()
        f = Path(tmp) / INSTALLED_LOG
        f.parent.mkdir(parents=True, exist_ok=True)
        if payload is not None:
            f.write_text(json.dumps(payload) if isinstance(payload, dict) else payload)
        p = mock.Mock(); p.drive_c = Path(tmp)
        return p

    def test_products_come_from_the_log(self):
        p = self._prefix({"installed": [
            {"product_id": "-2", "product_name": "Arturia Software Center", "version": "2.12.0"},
            {"product_id": "430", "product_name": "Analog Lab V", "version": "5.12.5.6878"}]})
        with mock.patch("vstenv.programs.installed", return_value=[]):
            got = vendors.get("arturia").products(p)
        self.assertEqual([(x.name, x.version) for x in got], [("Analog Lab V", "5.12.5.6878")])

    def test_the_software_center_itself_is_not_a_product(self):
        p = self._prefix({"installed": [
            {"product_id": "-2", "product_name": "Arturia Software Center", "version": "2.12.0"}]})
        with mock.patch("vstenv.programs.installed", return_value=[]):
            self.assertEqual(vendors.get("arturia").products(p), [])

    def test_a_missing_log_is_not_an_error(self):
        p = self._prefix(None)
        with mock.patch("vstenv.programs.installed", return_value=[]):
            self.assertEqual(vendors.get("arturia").products(p), [])

    def test_a_corrupt_log_is_not_an_error(self):
        p = self._prefix("{not json")
        with mock.patch("vstenv.programs.installed", return_value=[]):
            self.assertEqual(vendors.get("arturia").products(p), [])

    def test_an_installed_program_the_log_missed_is_still_listed(self):
        p = self._prefix({"installed": []})
        prog = mock.Mock(version="3.1", install_dir="C:/x", publisher="Arturia")
        prog.name = "Pigments 3.1"
        with mock.patch("vstenv.programs.installed", return_value=[prog]):
            got = vendors.get("arturia").products(p)
        self.assertEqual([x.name for x in got], ["Pigments"])

    def test_a_product_in_both_is_listed_once(self):
        p = self._prefix({"installed": [
            {"product_id": "430", "product_name": "Analog Lab V", "version": "5.12.5.6878"}]})
        prog = mock.Mock(version="5.12.5", install_dir="C:/x", publisher="Arturia")
        prog.name = "Analog Lab V 5.12.5"
        with mock.patch("vstenv.programs.installed", return_value=[prog]):
            got = vendors.get("arturia").products(p)
        self.assertEqual([x.name for x in got], ["Analog Lab V"])

    def test_no_licence_state_is_claimed(self):
        """Arturia keeps licences in a binary lic.ascd this does not read."""
        p = self._prefix({"installed": [
            {"product_id": "430", "product_name": "Analog Lab V", "version": "5.12.5"}]})
        with mock.patch("vstenv.programs.installed", return_value=[]):
            self.assertIsNone(vendors.get("arturia").products(p)[0].licensed)


if __name__ == "__main__":
    unittest.main()
