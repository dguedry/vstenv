"""7-Zip and cabinets without depending on what the host has."""
import os, struct, subprocess, tempfile, unittest
from pathlib import Path
from unittest import mock
from vstenv import tools

class SevenZipTest(unittest.TestCase):
    def test_version_parses_every_banner_style(self):
        for banner, want in (("7-Zip (z) 26.03 (x64) : Copyright", (26, 3)), ("7-Zip [64] 16.02 : Copyright", (16, 2)),
                             ("7-Zip 23.01 (x64) : Copyright", (23, 1)), ("7-Zip (a) 24.09 (x64)", (24, 9)), ("not 7-zip", None)):
            with mock.patch.object(subprocess, "run", return_value=subprocess.CompletedProcess([], 0, stdout=banner, stderr="")):
                self.assertEqual(tools.seven_zip_version("x"), want, banner)
    def test_host_seven_zip_is_used_only_when_new_enough(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(tools, "OWN", Path(tmp) / "7zz"):
            with mock.patch.object(tools.shutil, "which", side_effect=lambda n: "/usr/bin/7z" if n == "7z" else None), \
                 mock.patch.object(tools, "seven_zip_version", return_value=(23, 1)):
                self.assertIsNone(tools.seven_zip_status()["path"])
            with mock.patch.object(tools.shutil, "which", side_effect=lambda n: "/usr/bin/7zz" if n == "7zz" else None), \
                 mock.patch.object(tools, "seven_zip_version", return_value=(24, 9)):
                self.assertEqual(tools.seven_zip_status(), {"path": "/usr/bin/7zz", "version": "24.09", "source": "host"})
    def test_downloaded_copy_wins(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(tools, "OWN", Path(tmp) / "7zz"):
            (Path(tmp) / "7zz").write_bytes(b"x")
            with mock.patch.object(tools, "seven_zip_version", return_value=(26, 3)):
                self.assertEqual(tools.seven_zip_status()["source"], "downloaded")
    def test_seven_zip_fetches_when_nothing_usable(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(tools, "OWN", Path(tmp) / "7zz"), \
             mock.patch.object(tools.shutil, "which", return_value=None), \
             mock.patch.object(tools, "install_seven_zip", return_value=Path(tmp) / "7zz") as inst:
            self.assertEqual(tools.seven_zip(), str(Path(tmp) / "7zz")); inst.assert_called_once()

class CabinetTest(unittest.TestCase):
    def test_cabinets_are_found_by_signature_and_size(self):
        def cab(payload: bytes) -> bytes:
            body = b"MSCF" + b"\0" * 4 + struct.pack("<I", 36 + len(payload)) + b"\0" * 24 + payload
            return body
        data = b"junk" * 10 + cab(b"one") + b"MSCFbroken" + b"x" * 5 + cab(b"second one")
        found = list(tools.cabinets(data))
        self.assertEqual([c[-len(p):] for c, p in zip(found, (b"one", b"second one"))], [b"one", b"second one"])
        self.assertEqual(len(found), 2)
    @unittest.skipUnless((tools.paths.DOWNLOADS / "VC_redist2019.x64.exe").exists() and tools.seven_zip_status()["path"], "needs the cached VC redist and a 7-Zip")
    def test_ucrtbase_comes_out_of_the_real_redistributable(self):
        with tempfile.TemporaryDirectory() as tmp:
            files = tools.extract_cabinets(tools.paths.DOWNLOADS / "VC_redist2019.x64.exe", Path(tmp))
            ucrt = [f for f in files if f.name.lower() == "ucrtbase.dll"]
            self.assertEqual(len(ucrt), 1); self.assertGreater(ucrt[0].stat().st_size, 900_000)

if __name__ == "__main__": unittest.main()
