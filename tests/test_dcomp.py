"""Programs and plugins that import dcomp.dll get Wine's own Direct3D: found by
import table, cached by size and mtime, matched by the path yabridge passes."""
import tempfile, unittest
from pathlib import Path
from unittest import mock
from vstenv import dcomp, pe, programs
from vstenv.wine import Prefix, WineBuild

class ImportScanTest(unittest.TestCase):
    def test_cache_by_size_and_mtime_avoids_rereading(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "x.exe"; f.write_bytes(b"MZ" + b"\0" * 100)
            with mock.patch.object(dcomp, "CACHE", Path(tmp) / "cache.json"), \
                 mock.patch.object(pe, "imports", return_value=["dcomp.dll"]) as imp:
                self.assertTrue(dcomp.imports_dcomp(f)); self.assertTrue(dcomp.imports_dcomp(f))
                self.assertEqual(imp.call_count, 1)                      # second answer came from the cache
                f.write_bytes(b"MZ" + b"\0" * 200)                       # a new build: rescanned
                imp.return_value = []
                self.assertFalse(dcomp.imports_dcomp(f)); self.assertEqual(imp.call_count, 2)

class ProgramsAndPluginsTest(unittest.TestCase):
    def _prefix(self, tmp):
        p = Prefix(Path(tmp) / "prefix", WineBuild(Path(tmp) / "wine")); (p.drive_c / "users/me").mkdir(parents=True)
        return p
    def test_program_found_by_exe_or_a_dll_beside_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = self._prefix(tmp)
            for rel in ("Program Files/Spitfire Audio/Spitfire Audio.exe", "Program Files/Steinberg/HALion Sonic/HALion Sonic.exe",
                        "Program Files/Steinberg/HALion Sonic/graphics2d.dll", "Program Files/FabFilter/FabFilter Pro-Q 4.exe"):
                f = p.drive_c / rel; f.parent.mkdir(parents=True, exist_ok=True); f.write_bytes(b"MZ")
            progs = [programs.Program(name="Spitfire Audio", exe=r"C:\Program Files\Spitfire Audio\Spitfire Audio.exe", install_dir=r"C:\Program Files\Spitfire Audio"),
                     programs.Program(name="HALion Sonic", exe=r"C:\Program Files\Steinberg\HALion Sonic\HALion Sonic.exe", install_dir=r"C:\Program Files\Steinberg\HALion Sonic"),
                     programs.Program(name="FabFilter", exe=r"C:\Program Files\FabFilter\FabFilter Pro-Q 4.exe", install_dir=r"C:\Program Files\FabFilter")]
            def imports(f): return ["dcomp.dll"] if f.name in ("Spitfire Audio.exe", "graphics2d.dll") else ["user32.dll"]
            with mock.patch.object(dcomp, "CACHE", Path(tmp) / "cache.json"), mock.patch.object(pe, "imports", side_effect=imports), \
                 mock.patch.object(programs, "installed", return_value=progs):
                self.assertEqual(dcomp.program_exes(p), ["HALion Sonic.exe", "Spitfire Audio.exe"])
                self.assertTrue(dcomp.is_dcomp_program(progs[0], ["Spitfire Audio.exe"])); self.assertFalse(dcomp.is_dcomp_program(progs[2], ["Spitfire Audio.exe"]))
    def test_plugin_entry_names_the_bundle_yabridge_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = self._prefix(tmp)
            vst3 = p.drive_c / "Program Files/Common Files/VST3"
            inner = vst3 / "Ozone 9.vst3/Contents/x86_64-win/Ozone 9.vst3"; inner.parent.mkdir(parents=True); inner.write_bytes(b"MZ")
            plain = vst3 / "Plain.vst3"; plain.write_bytes(b"MZ")
            clap = p.drive_c / "Program Files/Common Files/CLAP/Thing.clap"; clap.parent.mkdir(parents=True); clap.write_bytes(b"MZ")
            def imports(f): return ["dcomp.dll"] if f.name in ("Ozone 9.vst3", "Thing.clap") else []
            from vstenv import yabridge
            with mock.patch.object(dcomp, "CACHE", Path(tmp) / "cache.json"), mock.patch.object(pe, "imports", side_effect=imports), \
                 mock.patch.object(yabridge, "plugin_dirs", return_value=[vst3, clap.parent]):
                out = dcomp.plugin_overrides(p)
            self.assertEqual(out, [(str(vst3 / "Ozone 9.vst3"), "d3d10core,d3d11,dxgi=b"), (str(clap), "d3d10core,d3d11,dxgi=b")])
    def test_overrides_applied_once_per_exe(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = self._prefix(tmp); reg = {}
            def add(key, name, value, kind="REG_SZ"): reg.setdefault(key, {})[name] = value
            with mock.patch.object(p, "reg_add", side_effect=add), mock.patch.object(p, "reg_query", side_effect=lambda k: reg.get(k, {})):
                self.assertEqual(dcomp.apply_program_overrides(p, ["A.exe", "A.exe"]), ["A.exe"])
                self.assertEqual(reg[dcomp.overrides_key("A.exe")], {"d3d10core": "builtin", "d3d11": "builtin", "dxgi": "builtin"})
                self.assertEqual(dcomp.apply_program_overrides(p, ["A.exe"]), [])

if __name__ == "__main__": unittest.main()
