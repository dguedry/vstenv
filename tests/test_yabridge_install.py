import tempfile
from pathlib import Path
from unittest import mock
import json, os, tempfile, unittest
from pathlib import Path
from vstenv import yabridge

class YabridgeInstallTest(unittest.TestCase):
    def test_pinned_wine_version_parses_build_name(self):
        v = yabridge.pinned_wine_version()
        self.assertRegex(v, r"^\d+\.\d+")

    def test_needs_master(self):
        self.assertFalse(yabridge.needs_master("9.21"))
        self.assertTrue(yabridge.needs_master("9.22"))
        self.assertTrue(yabridge.needs_master("11.17"))
        self.assertFalse(yabridge.needs_master(""))

    def test_pick_asset_exact_wine_version_only(self):
        assets = [{"name": "vstenv.flatpak", "browser_download_url": "u0"},
                  {"name": "yabridge-b580a9f-wine-11.16.tar.gz", "browser_download_url": "u1"},
                  {"name": "yabridge-b580a9f-wine-11.17.tar.gz", "browser_download_url": "u2"}]
        self.assertEqual(yabridge.pick_asset(assets, "11.17")["browser_download_url"], "u2")
        self.assertIsNone(yabridge.pick_asset(assets, "11.18"))
        self.assertIsNone(yabridge.pick_asset([], "11.17"))

    def test_compatibility_with_marker(self):
        with tempfile.TemporaryDirectory() as d:
            old_dir, old_yctl, old_marker = yabridge.YAB_DIR, yabridge.YCTL, yabridge.MARKER
            yabridge.YAB_DIR = Path(d); yabridge.YCTL = Path(d) / "yabridgectl"; yabridge.MARKER = Path(d) / "nilinux-build.json"
            try:
                self.assertEqual(yabridge.compatibility()[0], False)          # not installed
                yabridge.YCTL.write_text("")
                ok, detail = yabridge.compatibility()                          # upstream release, new wine
                self.assertFalse(ok); self.assertIn("mouse clicks", detail)
                yabridge.MARKER.write_text(json.dumps({"yabridge_commit": "abc1234", "wine_version": yabridge.pinned_wine_version()}))
                ok, detail = yabridge.compatibility()
                self.assertTrue(ok); self.assertIn("abc1234", detail)
                self.assertIn("abc1234", yabridge.installed())
                yabridge.MARKER.write_text(json.dumps({"yabridge_commit": "abc1234", "wine_version": "9.21"}))
                ok, detail = yabridge.compatibility()
                self.assertFalse(ok); self.assertIn("built for wine 9.21", detail)
            finally:
                yabridge.YAB_DIR, yabridge.YCTL, yabridge.MARKER = old_dir, old_yctl, old_marker

if __name__ == "__main__": unittest.main()


class InstallTarballTest(unittest.TestCase):
    """The yabridge directory is a Flatpak bind mount: it must never be renamed
    or removed, only its contents replaced (see the EBUSY report)."""

    def _tarball(self, root: Path, marker: bytes, with_leading_dir=True) -> Path:
        import tarfile
        src = root / "pkg/yabridge"; src.mkdir(parents=True)
        for name in ("libyabridge-vst3.so", "yabridge-host.exe", "yabridgectl"):
            (src / name).write_bytes(marker + b" " + name.encode())
        tgz = root / "yabridge.tar.gz"
        with tarfile.open(tgz, "w:gz") as t:
            t.add(src, arcname="yabridge" if with_leading_dir else ".")
        return tgz

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.yab = self.root / "share/yabridge"
        self.patches = [mock.patch.object(yabridge, "YAB_DIR", self.yab),
                        mock.patch.object(yabridge, "YCTL", self.yab / "yabridgectl"),
                        mock.patch.object(yabridge.Path, "home", staticmethod(lambda: self.root))]
        for p in self.patches: p.start()

    def tearDown(self):
        for p in self.patches: p.stop()
        self.tmp.cleanup()

    def test_installs_into_an_existing_directory_without_renaming_it(self):
        self.yab.mkdir(parents=True)
        (self.yab / "libyabridge-vst3.so").write_bytes(b"OLD libyabridge-vst3.so")
        before = self.yab.stat().st_ino                      # the mount point's identity
        yabridge._install_tarball(self._tarball(self.root, b"NEW"), None)
        self.assertEqual(self.yab.stat().st_ino, before, "the directory itself must not be replaced")
        self.assertTrue((self.yab / "libyabridge-vst3.so").read_bytes().startswith(b"NEW"))
        self.assertTrue((self.yab / "yabridge-host.exe").exists())
        self.assertFalse((self.yab.parent / "yabridge.bak").exists(), "no sibling backup dir")
        self.assertTrue((self.yab / "previous/libyabridge-vst3.so").read_bytes().startswith(b"OLD"))

    def test_fails_loudly_rather_than_half_installing_when_the_rename_would_be_needed(self):
        """Regression: renaming the directory raised EBUSY inside Flatpak."""
        self.yab.mkdir(parents=True)
        real_rename = Path.rename
        def no_rename(self_, target):                        # any attempt to move the dir is a bug
            if Path(self_) == self.yab: raise OSError(16, "Device or resource busy")
            return real_rename(self_, target)
        with mock.patch.object(Path, "rename", no_rename):
            yabridge._install_tarball(self._tarball(self.root, b"NEW"), None)
        self.assertTrue((self.yab / "yabridgectl").exists())

    def test_tarball_without_a_leading_directory(self):
        self.yab.mkdir(parents=True)
        yabridge._install_tarball(self._tarball(self.root, b"FLAT", with_leading_dir=False), None)
        self.assertTrue((self.yab / "yabridge-host.exe").read_bytes().startswith(b"FLAT"))

    def test_creates_the_directory_when_absent(self):
        yabridge._install_tarball(self._tarball(self.root, b"NEW"), None)
        self.assertTrue((self.yab / "yabridgectl").exists())
        self.assertTrue((self.root / ".local/bin/yabridgectl").is_symlink())
