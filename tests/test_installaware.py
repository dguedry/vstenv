import tempfile, unittest
from pathlib import Path
from vstenv.installers import installaware
from vstenv.vendors.ni import products

class FakePrefix:
    def __init__(self, root):
        self.path = Path(root); self.drive_c = self.path / "drive_c"
        self.user_dir = self.drive_c / "users" / "me"
    def to_host(self, winpath): return self.drive_c / winpath.replace("\\", "/")[3:]
    def reg_query(self, key): return {"DownloadLocation": r"C:\users\Public\Downloads"}

class StagedTest(unittest.TestCase):
    def make(self, root, name="Kontakt 8", with_download=True):
        p = FakePrefix(root)
        d = p.user_dir / "AppData/Local/Temp/mia1aa5.tmp"; (d / "data" / "OFFLINE").mkdir(parents=True)
        (d / f"{name} Setup PC.exe").write_bytes(b"MZ"); (d / "data" / f"{name} Setup PC.msi").write_bytes(b"msi")
        (p.user_dir / "AppData/Local/Temp/miaXYZ.tmp").mkdir()          # unrelated temp folder: no exe/msi
        dl = p.drive_c / "users/Public/Downloads"; dl.mkdir(parents=True)
        if with_download: (dl / "Kontakt_8_Installer.zip").write_bytes(b"PK")
        return p

    def test_detects_staged_install_and_its_download(self):
        with tempfile.TemporaryDirectory() as root:
            p = self.make(root)
            from vstenv.vendors.ni import native_access as na
            orig = na.download_location_status
            na.download_location_status = lambda pr: (r"C:\users\Public\Downloads", True)
            try: st = installaware.staged_installs(p, products.find_download)
            finally: na.download_location_status = orig
            self.assertEqual(len(st), 1)
            self.assertEqual(st[0].name, "Kontakt 8")
            self.assertEqual(st[0].msi.name, "Kontakt 8 Setup PC.msi")
            self.assertIsNotNone(st[0].download)
            self.assertEqual(st[0].download.name, "Kontakt_8_Installer.zip")

    def test_no_download_is_reported_as_none(self):
        with tempfile.TemporaryDirectory() as root:
            p = self.make(root, with_download=False)
            from vstenv.vendors.ni import native_access as na
            orig = na.download_location_status
            na.download_location_status = lambda pr: ("", False)
            try: st = installaware.staged_installs(p, products.find_download)
            finally: na.download_location_status = orig
            self.assertEqual(len(st), 1)
            self.assertIsNone(st[0].download)

    def test_nothing_staged(self):
        with tempfile.TemporaryDirectory() as root:
            p = FakePrefix(root); (p.user_dir / "AppData/Local/Temp").mkdir(parents=True)
            self.assertEqual(installaware.staged_installs(p, products.find_download), [])

if __name__ == "__main__": unittest.main()
