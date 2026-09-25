"""Steinberg packages: the Download Assistant's Install Assistant refuses them
under Wine (signed prerun scripts); vstenv finishes them from the package."""
import json, subprocess, tempfile, unittest, zipfile
from pathlib import Path
from unittest import mock
from vstenv.installers import steinberg as pkg
from vstenv.wine import Prefix, WineBuild

SETUP_XML = """<?xml version='1.0'?>
<Setup>
  <product file="MediaBay.msi" arm64compatible="true"><title>MediaBay</title></product>
  <include>
    <prerun prerun="Additional Content/Installer Data/preinstall.ps1" package="MediaBay.msi"/>
    <path msi="Additional Content/Installer"/>
  </include>
  <msiPackage file="MediaBay.msi"/>
  <msiPackage file="LibraryManager.msi"/>
  <libraryManager name="VST Sound Content Update" source="x" customDir="default"/>
</Setup>"""

def make_zip(path: Path):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("MediaBay 1.3.100/setup.xml", SETUP_XML)
        z.writestr("MediaBay 1.3.100/MediaBay.msi", "msi")
        z.writestr("MediaBay 1.3.100/Additional Content/Installer/LibraryManager.msi", "msi")
        z.writestr("MediaBay 1.3.100/Additional Content/Installer Data/preinstall.ps1", "exit 0")

class PackageTest(unittest.TestCase):
    def test_setup_xml_lists_product_msi_first_then_the_others(self):
        with tempfile.TemporaryDirectory() as tmp:
            x = Path(tmp) / "setup.xml"; x.write_text(SETUP_XML)
            s = pkg.parse_setup(x)
            self.assertEqual((s.title, s.msis), ("MediaBay", ["MediaBay.msi", "LibraryManager.msi"]))
            self.assertEqual(s.prerun, ["Additional Content/Installer Data/preinstall.ps1"]); self.assertEqual(len(s.library), 1)
    def test_zip_with_setup_xml_is_a_package(self):
        with tempfile.TemporaryDirectory() as tmp:
            z = Path(tmp) / "MediaBay_Installer_win64.zip"; make_zip(z)
            self.assertTrue(pkg.is_package(z)); self.assertFalse(pkg.is_package(Path(tmp) / "nope.zip"))
    def test_tokens_match_status_file_to_package(self):
        self.assertEqual(pkg._token("Steinberg_MediaBay_Installer.json"), pkg._token("MediaBay_Installer_win64.zip"))
        self.assertEqual(pkg._token("Steinberg_Library_Manager_Installer.json"), pkg._token("Steinberg_Library_Manager_win64.zip"))
        self.assertEqual(pkg._token("Steinberg_Install_Assistant_Installer.json"), pkg._token("Steinberg_Install_Assistant_Installer_win.exe"))
        self.assertNotEqual(pkg._token("Steinberg_MediaBay_Installer.json"), pkg._token("Steinberg_Library_Manager_win64.zip"))
    def test_install_runs_every_msi_silently_and_skips_the_prerun(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Prefix(Path(tmp) / "prefix", WineBuild(Path(tmp) / "wine")); p.drive_c.mkdir(parents=True)
            z = Path(tmp) / "MediaBay_Installer_win64.zip"; make_zip(z)
            calls = []
            def run(argv, **kw): calls.append(argv); return subprocess.CompletedProcess(argv, 0)
            with mock.patch.object(p, "run", side_effect=run), mock.patch.object(pkg.paths, "CACHE", Path(tmp)):
                res = pkg.install(p, z)
            self.assertEqual(res["name"], "MediaBay"); self.assertEqual(res["installed"], ["MediaBay.msi", "LibraryManager.msi"]); self.assertEqual(res["failed"], [])
            self.assertTrue(all(a[0] == "msiexec" and "/qn" in a for a in calls)); self.assertEqual(len(calls), 2)
            self.assertFalse(any("preinstall" in " ".join(a) for a in calls))
    def test_staged_finds_failed_status_files_with_their_package(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Prefix(Path(tmp) / "prefix", WineBuild(Path(tmp) / "wine")); (p.drive_c / "users/me").mkdir(parents=True)
            job = p.user_dir / "AppData/Local/Temp/6dcf"; job.mkdir(parents=True)
            make_zip(job / "MediaBay_Installer_win64.zip")
            (job / "Steinberg_MediaBay_Installer.json").write_text(json.dumps({"artifactId": "Steinberg_MediaBay_Installer", "success": False, "errors": [{"code": "231", "message": "preinstall.ps1: not trusted."}]}))
            (job / "Steinberg_Library_Manager_Installer.json").write_text(json.dumps({"success": True}))
            make_zip(job / "Steinberg_Library_Manager_win64.zip")
            st = pkg.staged(p)
            self.assertEqual([(s.name, s.package.name) for s in st], [("MediaBay", "MediaBay_Installer_win64.zip")])

if __name__ == "__main__": unittest.main()
