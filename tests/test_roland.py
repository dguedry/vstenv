import re
import unittest
from pathlib import Path
from unittest import mock

from vstenv import vendors
from vstenv.vendors.roland import INSTALLER_ENV, LAUNCH_ENV, MANAGER_EXE


class RolandVendorTest(unittest.TestCase):
    def setUp(self):
        self.v = vendors.get("roland")

    def test_registered_as_a_builtin(self):
        self.assertIn("roland", [v.id for v in vendors.all()])

    def test_recognises_its_installer_by_name(self):
        self.assertTrue(self.v.accepts_manager_installer("Roland-Cloud-Manager-Installer-3-1-25.exe"))
        self.assertTrue(self.v.accepts_manager_installer("roland_cloud_manager.exe"))

    def test_does_not_claim_another_vendor_installer(self):
        for other in ("Native-Access-2.exe", "SDA_Installer.exe", "ik_product_manager.exe"):
            self.assertFalse(self.v.accepts_manager_installer(other), other)

    def test_only_one_vendor_claims_the_roland_installer(self):
        """Two vendors claiming one file would make classify_installer ambiguous."""
        name = "Roland-Cloud-Manager-Installer-3-1-25.exe"
        claimers = [v.id for v in vendors.all() if v.accepts_manager_installer(name)]
        self.assertEqual(claimers, ["roland"])

    def test_qt_variable_uses_the_name_wine_passes_through(self):
        """Wine drops QT_*; only WINEQT_<name> arrives as QT_<name>, so a plain
        QT_QPA_PLATFORMTHEME here would silently do nothing."""
        self.assertIn("WINEQT_QPA_PLATFORMTHEME", LAUNCH_ENV)
        self.assertEqual(LAUNCH_ENV["WINEQT_QPA_PLATFORMTHEME"], "windows")
        self.assertNotIn("QT_QPA_PLATFORMTHEME", LAUNCH_ENV)

    def test_installer_disables_netprofm(self):
        """Without it the Qt Installer Framework setup answers every command
        with "Unknown exception caught." under Wine."""
        self.assertIn("netprofm=d", INSTALLER_ENV.get("WINEDLLOVERRIDES", ""))

    def test_launch_env_is_offered_for_its_programs(self):
        self.assertEqual(self.v.launch_env(mock.Mock(), mock.Mock()), LAUNCH_ENV)

    def test_the_helper_service_is_known(self):
        """RCMservice.exe outlives the window; the app has to know it is ours."""
        self.assertIn("RCMservice.exe", self.v.manager_exe_names())
        self.assertIn(MANAGER_EXE, self.v.manager_exe_names())


class RolandInstallTest(unittest.TestCase):
    """The Qt Installer Framework setup cannot show its own interface here, so
    the whole install goes on the command line."""

    def test_install_runs_the_setup_headless_and_checks_the_result(self):
        v = vendors.get("roland")
        p = mock.Mock()
        p.run.return_value = mock.Mock(stdout="", stderr="", returncode=0)
        with mock.patch.object(type(v), "_manager_exe", lambda self, _p: Path("/x/Roland Cloud Manager.exe")), \
             mock.patch.object(type(v), "manager_version", lambda self, _p: "3.1.25"):
            v.install_manager(p, Path("/tmp/setup.exe"))
        args = p.run.call_args
        self.assertIn("install", args[0][0])
        self.assertIn("--accept-licenses", args[0][0])
        self.assertIn("--confirm-command", args[0][0])
        self.assertEqual(args[1]["env"], INSTALLER_ENV)

    def test_an_install_that_wrote_nothing_is_an_error(self):
        """IFW can exit cleanly having installed nothing; the file is the proof."""
        v = vendors.get("roland")
        p = mock.Mock()
        p.run.return_value = mock.Mock(stdout="", stderr="some failure", returncode=0)
        with mock.patch.object(type(v), "_manager_exe", lambda self, _p: None):
            with self.assertRaises(RuntimeError) as e:
                v.install_manager(p, Path("/tmp/setup.exe"))
        self.assertIn(MANAGER_EXE, str(e.exception))


if __name__ == "__main__":
    unittest.main()
