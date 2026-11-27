"""winefixes: patched DLLs go into the Wine build, verified by hash, restorable."""
import hashlib, json, tarfile, tempfile, unittest
from pathlib import Path
from unittest import mock
from vstenv import winefixes, wine

def _fake_build(tmp: Path) -> wine.WineBuild:
    root = tmp / "wine-11.17-staging-amd64-wow64"
    (root / winefixes.PE_DIR).mkdir(parents=True); (root / "bin").mkdir()
    b = wine.WineBuild(root)
    for name in winefixes.FIXES:
        f = winefixes.dest(b, name); f.parent.mkdir(parents=True, exist_ok=True); f.write_bytes(b"stock " + name.encode())
    return b

def _tarball(tmp: Path, version="11.17", payload=b"patched dcomp") -> Path:
    """A release tarball carrying every DLL the app expects (FIXES), dcomp with `payload`."""
    pkg = tmp / "wine-fixes"; pkg.mkdir(); files = {}
    for name in winefixes.FIXES:
        data = payload if name == "dcomp.dll" else b"patched " + name.encode()
        (pkg / name).parent.mkdir(parents=True, exist_ok=True)
        (pkg / name).write_bytes(data); files[name] = hashlib.sha256(data).hexdigest()
    (pkg / "manifest.json").write_text(json.dumps({"wine_version": version, "files": files, "patches": ["x.patch"]}))
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
                self.assertEqual(sorted(winefixes.install(b)), sorted(winefixes.FIXES))
            dll = b.root / winefixes.PE_DIR / "dcomp.dll"
            self.assertEqual(dll.read_bytes(), b"patched dcomp"); self.assertEqual(dll.with_suffix(".dll.orig").read_bytes(), b"stock dcomp.dll")
            self.assertTrue(winefixes.has(b, "dcomp.dll")); self.assertTrue(winefixes.status(b)["installed"])
            dll.write_bytes(b"stock dcomp")                      # a re-extracted Wine put the stock file back
            self.assertFalse(winefixes.has(b, "dcomp.dll"), "the marker alone does not count")
            self.assertEqual(sorted(winefixes.remove(b)), sorted(winefixes.FIXES)); self.assertFalse((b.root / winefixes.MARKER_NAME).exists())
    def test_missing_keys_are_reported_and_a_short_tarball_installs_what_it_has(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); b = _fake_build(tmp); tgz = _tarball(tmp)
            with mock.patch.dict("os.environ", {"VSTENV_WINE_FIXES": str(tgz)}): winefixes.install(b)
            self.assertTrue(winefixes.status(b)["installed"])
            winefixes.dest(b, "x86_64-unix/win32u.so").write_bytes(b"stock again")   # re-extracted Wine
            st = winefixes.status(b); self.assertFalse(st["installed"]); self.assertEqual(st["missing"], ["x86_64-unix/win32u.so"])
            m = json.loads((b.root / winefixes.MARKER_NAME).read_text())             # an older marker without the newer keys
            m["files"].pop("x86_64-unix/win32u.so")
            (b.root / winefixes.MARKER_NAME).write_text(json.dumps(m))
            self.assertEqual(winefixes.status(b)["missing"], ["x86_64-unix/win32u.so"])
            with mock.patch.dict("os.environ", {"VSTENV_WINE_FIXES": str(tgz)}): winefixes.install(b)   # reinstalls what is missing
            self.assertTrue(winefixes.status(b)["installed"])
    def test_a_tarball_without_some_keys_still_installs_the_rest(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); b = _fake_build(tmp); tgz = _tarball(tmp)
            with tarfile.open(tgz) as t: t.extractall(tmp / "x", filter="tar")
            man = tmp / "x/wine-fixes/manifest.json"; m = json.loads(man.read_text())
            m["files"].pop("x86_64-unix/win32u.so")
            man.write_text(json.dumps(m))
            old = tmp / "old.tar.gz"
            with tarfile.open(old, "w:gz") as t: t.add(tmp / "x/wine-fixes", arcname="wine-fixes")
            with mock.patch.dict("os.environ", {"VSTENV_WINE_FIXES": str(old)}): got = winefixes.install(b)
            self.assertEqual(sorted(got), sorted(set(winefixes.FIXES) - {"x86_64-unix/win32u.so"}))
            self.assertEqual(sorted(winefixes.status(b)["missing"]), ["x86_64-unix/win32u.so"])
    def test_install_refuses_a_tarball_for_another_wine(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); b = _fake_build(tmp); tgz = _tarball(tmp, version="10.0")
            with mock.patch.dict("os.environ", {"VSTENV_WINE_FIXES": str(tgz)}):
                self.assertEqual(winefixes.install(b), {})
            self.assertEqual((b.root / winefixes.PE_DIR / "dcomp.dll").read_bytes(), b"stock dcomp.dll")
    def test_offline_install_fails_softly_and_keeps_what_is_in_place(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); b = _fake_build(tmp); tgz = _tarball(tmp)
            with mock.patch.dict("os.environ", {"VSTENV_WINE_FIXES": ""}), mock.patch.object(winefixes, "text", side_effect=OSError("offline")):
                self.assertEqual(winefixes.install(b), {})
            with mock.patch.dict("os.environ", {"VSTENV_WINE_FIXES": str(tgz)}): winefixes.install(b)
            with mock.patch.dict("os.environ", {"VSTENV_WINE_FIXES": ""}), mock.patch.object(winefixes, "text", side_effect=OSError("offline")):
                self.assertEqual(sorted(winefixes.install(b)), sorted(winefixes.FIXES), "offline: the installed set stays")
    def test_a_newer_release_replaces_the_installed_set(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); b = _fake_build(tmp)
            def release(tag, payload):
                rel = {"tag_name": tag, "assets": [{"name": "wine-fixes-11.17.tar.gz", "browser_download_url": "http://x/" + tag}]}
                tgz = _tarball(tmp / tag, payload=payload) if (tmp / tag).mkdir() is None else None
                return mock.patch.object(winefixes, "text", return_value=json.dumps(rel)), mock.patch.object(winefixes, "fetch", return_value=tgz)
            with mock.patch.dict("os.environ", {"VSTENV_WINE_FIXES": ""}):
                p1, p2 = release("v0.2.0", b"first")
                with p1, p2: winefixes.install(b)
                self.assertEqual((b.root / winefixes.PE_DIR / "dcomp.dll").read_bytes(), b"first"); self.assertEqual(winefixes.marker(b)["release"], "v0.2.0")
                with p1, p2 as fetch_mock: winefixes.install(b)
                fetch_mock.assert_not_called()                      # same release: nothing fetched, nothing touched
                p3, p4 = release("v0.2.1", b"second")
                with p3, p4: winefixes.install(b)
                self.assertEqual((b.root / winefixes.PE_DIR / "dcomp.dll").read_bytes(), b"second"); self.assertEqual(winefixes.marker(b)["release"], "v0.2.1")
                self.assertEqual((b.root / winefixes.PE_DIR / "dcomp.dll.orig").read_bytes(), b"stock dcomp.dll", "the stock file stays the original")
    def test_remove_deletes_additions_that_have_no_original(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); b = _fake_build(tmp); tgz = _tarball(tmp)
            winefixes.dest(b, "wineasio.dll").unlink()          # WineASIO is not in stock Wine: no .orig is made
            with mock.patch.dict("os.environ", {"VSTENV_WINE_FIXES": str(tgz)}): winefixes.install(b)
            self.assertFalse(winefixes.dest(b, "wineasio.dll").with_suffix(".dll.orig").exists())
            winefixes.remove(b); self.assertFalse(winefixes.dest(b, "wineasio.dll").exists())
class ForeignDllsTest(unittest.TestCase):
    """A patched DLL in the build is a different size from the prefix's stock copy; not 'another Wine'."""
    def test_patched_dll_is_not_foreign_but_others_still_are(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); b = _fake_build(tmp); p = wine.Prefix(tmp / "prefix", b)
            for sysdir, pedir, dll in p.PROBE_DLLS:
                for f, data in ((b.root / "lib/wine" / pedir / dll, b"ours"), (p.drive_c / "windows" / sysdir / dll, b"theirs!")):
                    f.parent.mkdir(parents=True, exist_ok=True); f.write_bytes(data)
            ntdll = winefixes.dest(b, "ntdll.dll"); ntdll.write_bytes(b"patched"); ntdll.with_suffix(".dll.orig").write_bytes(b"theirs!")
            self.assertEqual(p.foreign_dlls(), ["syswow64/comctl32.dll", "system32/comctl32.dll", "syswow64/user32.dll"])
            (p.drive_c / "windows/system32/ntdll.dll").write_bytes(b"another wine's")   # neither the patched nor the stock size
            self.assertIn("system32/ntdll.dll", p.foreign_dlls())
            (p.drive_c / "windows/system32/ntdll.dll").write_bytes(b"patched")          # refreshed prefix
            self.assertNotIn("system32/ntdll.dll", p.foreign_dlls())

if __name__ == "__main__": unittest.main()
