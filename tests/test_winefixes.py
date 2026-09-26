"""winefixes: patched DLLs go into the Wine build, verified by hash, restorable."""
import hashlib, json, tarfile, tempfile, unittest
from pathlib import Path
from unittest import mock
from vstenv import winefixes, wine

def _fake_build(tmp: Path) -> wine.WineBuild:
    root = tmp / "wine-11.17-staging-amd64-wow64"
    (root / winefixes.PE_DIR).mkdir(parents=True); (root / "bin").mkdir()
    (root / winefixes.PE_DIR / "dcomp.dll").write_bytes(b"stock dcomp")
    return wine.WineBuild(root)

def _tarball(tmp: Path, version="11.17", payload=b"patched dcomp") -> Path:
    pkg = tmp / "wine-fixes"; pkg.mkdir()
    (pkg / "dcomp.dll").write_bytes(payload)
    (pkg / "manifest.json").write_text(json.dumps({"wine_version": version, "files": {"dcomp.dll": hashlib.sha256(payload).hexdigest()}, "patches": ["x.patch"]}))
    tgz = tmp / f"wine-fixes-{version}.tar.gz"
    with tarfile.open(tgz, "w:gz") as t: t.add(pkg, arcname="wine-fixes")
    return tgz

class WineFixesTest(unittest.TestCase):
    def test_version_comes_from_the_build_name(self):
        self.assertEqual(winefixes.wine_version(), "11.17"); self.assertEqual(winefixes.asset_name(), "wine-fixes-11.17.tar.gz")
    def test_install_keeps_the_original_and_verifies_by_hash(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); b = _fake_build(tmp); tgz = _tarball(tmp)
            self.assertFalse(winefixes.status(b)["installed"])
            with mock.patch.dict("os.environ", {"VSTENV_WINE_FIXES": str(tgz)}):
                self.assertEqual(list(winefixes.install(b)), ["dcomp.dll"])
            dll = b.root / winefixes.PE_DIR / "dcomp.dll"
            self.assertEqual(dll.read_bytes(), b"patched dcomp"); self.assertEqual(dll.with_suffix(".dll.orig").read_bytes(), b"stock dcomp")
            self.assertTrue(winefixes.has(b, "dcomp.dll")); self.assertTrue(winefixes.status(b)["installed"])
            dll.write_bytes(b"stock dcomp")                      # a re-extracted Wine put the stock file back
            self.assertFalse(winefixes.has(b, "dcomp.dll"), "the marker alone does not count")
            self.assertEqual(winefixes.remove(b), ["dcomp.dll"]); self.assertFalse((b.root / winefixes.MARKER_NAME).exists())
    def test_install_refuses_a_tarball_for_another_wine(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); b = _fake_build(tmp); tgz = _tarball(tmp, version="10.0")
            with mock.patch.dict("os.environ", {"VSTENV_WINE_FIXES": str(tgz)}):
                self.assertEqual(winefixes.install(b), {})
            self.assertEqual((b.root / winefixes.PE_DIR / "dcomp.dll").read_bytes(), b"stock dcomp")
    def test_offline_install_fails_softly(self):
        with tempfile.TemporaryDirectory() as d:
            b = _fake_build(Path(d))
            with mock.patch.dict("os.environ", {"VSTENV_WINE_FIXES": ""}), mock.patch.object(winefixes, "text", side_effect=OSError("offline")):
                self.assertEqual(winefixes.install(b), {})

if __name__ == "__main__": unittest.main()
