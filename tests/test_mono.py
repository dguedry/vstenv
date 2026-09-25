import tempfile, unittest
from pathlib import Path
from unittest import mock
from vstenv import mono
from vstenv.wine import Prefix, WineBuild

class MonoTest(unittest.TestCase):
    def _build(self, tmp, mscoree: bytes):
        root = Path(tmp) / "wine"; d = root / "lib/wine/x86_64-windows"; d.mkdir(parents=True)
        (d / "mscoree.dll").write_bytes(mscoree); return WineBuild(root)
    def test_required_version_is_read_next_to_the_mono_search_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = self._build(tmp, b"\x00" * 100 + b"Wine Mono Windows Support\x00\\mono\x00\\..\\mono\x0011.3.0\x00" + b"\x00" * 100)
            self.assertEqual(mono.required_version(b), "11.3.0")
    def test_required_version_falls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(mono.required_version(self._build(tmp, b"nothing here")), mono.FALLBACK_VERSION)
            self.assertEqual(mono.required_version(WineBuild(Path(tmp) / "missing")), mono.FALLBACK_VERSION)
    def test_installed_and_overrides_follow_the_mono_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Prefix(Path(tmp) / "prefix", WineBuild(Path(tmp) / "wine"))
            (p.drive_c / "windows").mkdir(parents=True)
            self.assertFalse(mono.installed(p)); self.assertIn("mscoree=d", p.dll_overrides())
            (p.drive_c / "windows/mono/mono-2.0").mkdir(parents=True)
            self.assertTrue(mono.installed(p)); self.assertNotIn("mscoree", p.dll_overrides())
            self.assertNotIn("mscoree", p.wine_env()["WINEDLLOVERRIDES"])
    def test_install_skips_when_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Prefix(Path(tmp) / "prefix", WineBuild(Path(tmp) / "wine"))
            (p.drive_c / "windows/mono/mono-2.0").mkdir(parents=True)
            with mock.patch.object(mono, "fetch") as fetch: self.assertFalse(mono.install(p)); fetch.assert_not_called()

if __name__ == "__main__": unittest.main()
