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


class InstallPidsTest(unittest.TestCase):
    def test_watches_stubs_inner_setup_and_msiexec(self):
        from unittest import mock
        from vstenv.installers import installaware
        p = mock.Mock()
        p.processes.return_value = [(1, "C:\\...\\Temp\\Kontakt_8_Installer\\Kontakt 8 8.13.1 Setup PC.exe /s"),
                                    (2, ".\\Kontakt 8 Setup PC.exe /s TARGETDIR=..."),
                                    (3, "C:\\windows\\syswow64\\msiexec.exe -Embedding 464"),
                                    (4, "C:\\Program Files\\Native Instruments\\Native Access\\Native Access.exe"),
                                    (5, "C:\\windows\\system32\\services.exe")]
        self.assertEqual(installaware.install_pids(p), [1, 2, 3])


class RescueWhileManagerOpenTest(unittest.TestCase):
    """The GUI's automatic rescue runs while Native Access is open (it drives the
    install), so waiting for the manager to exit meant skipping every install
    it had just stopped (Absynth 5, 2026-10-16). Only the product's own setup
    still running is a reason to wait."""

    def test_ni_does_not_wait_for_native_access(self):
        import types
        from vstenv.vendors.ni import NativeInstruments
        v = NativeInstruments()
        p = types.SimpleNamespace(is_running=lambda exe: exe == "Native Access.exe")
        self.assertFalse(v._busy(p, "Absynth 5"))

    def test_finish_still_waits_for_the_products_own_setup(self):
        import types
        from unittest import mock
        from vstenv.installers import installaware as ia
        st = types.SimpleNamespace(name="Absynth 5", dir=None, exe=None, msi=None, download=None)
        p = types.SimpleNamespace(is_running=lambda exe: exe == "Absynth 5 Setup")
        logs = []
        r = types.SimpleNamespace(step=lambda *a: None, skip=lambda m: logs.append(m), log=lambda *a: None, ok=lambda *a: None, fail=lambda *a: None)
        with mock.patch.object(ia, "staged_installs", return_value=[st]), mock.patch.object(ia, "_finish_from_staged", side_effect=AssertionError("must not run")):
            self.assertEqual(ia.finish_staged(p, r, None, lambda p, n: False), [])
        self.assertTrue(any("still running" in m for m in logs))
