import struct
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from vstenv import pe, plugfiles
from vstenv.progress import Reporter


def make_pe(exports=(), machine=0x8664) -> bytes:
    """A minimal PE32+ (or PE32 for x86) with one section holding an export table."""
    pe32plus = machine != 0x14c
    opt_size = 240 if pe32plus else 224
    sec_raw, sec_rva = 0x200, 0x1000
    # export directory (40 bytes), then the name pointer table, then the names
    names = [n.encode() + b"\0" for n in exports]
    npt_off = 40; names_off = npt_off + 4 * len(names)
    blob = bytearray(40)
    struct.pack_into("<I", blob, 24, len(names)); struct.pack_into("<I", blob, 32, sec_rva + npt_off)
    ptrs = bytearray(); data = bytearray(); off = names_off
    for n in names: ptrs += struct.pack("<I", sec_rva + off); data += n; off += len(n)
    section = bytes(blob + ptrs + data).ljust(0x200, b"\0")
    hdr = bytearray(0x200)
    hdr[:2] = b"MZ"; struct.pack_into("<I", hdr, 0x3c, 0x40)
    hdr[0x40:0x44] = b"PE\0\0"
    struct.pack_into("<HHIIIHH", hdr, 0x44, machine, 1, 0, 0, 0, opt_size, 0x2022)
    opt = 0x58
    struct.pack_into("<H", hdr, opt, 0x20B if pe32plus else 0x10B)
    dirs = opt + (112 if pe32plus else 96)
    if exports: struct.pack_into("<II", hdr, dirs, sec_rva, len(section))
    s = opt + opt_size
    hdr[s:s + 8] = b".edata\0\0"
    struct.pack_into("<IIII", hdr, s + 8, len(section), sec_rva, len(section), sec_raw)
    return bytes(hdr) + section

VST2 = make_pe(["VSTPluginMain", "main"]); VST2_32 = make_pe(["VSTPluginMain", "main"], 0x14c)
VST3 = make_pe(["GetPluginFactory"]); CLAP = make_pe(["clap_entry"]); HELPER = make_pe(["DoThing"])


class PeTest(unittest.TestCase):
    def test_machine_and_exports(self):
        with tempfile.TemporaryDirectory() as d:
            a = Path(d, "a.dll"); a.write_bytes(VST2); b = Path(d, "b.dll"); b.write_bytes(VST2_32)
            self.assertEqual(pe.machine(a), "x64"); self.assertEqual(pe.machine(b), "x86")
            self.assertEqual(pe.exports(a), ["VSTPluginMain", "main"])
            t = Path(d, "t.txt"); t.write_text("hi")
            self.assertIsNone(pe.machine(t)); self.assertEqual(pe.exports(t), [])


class InstallTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.d = Path(self.tmp.name)
        self.p = SimpleNamespace(drive_c=self.d / "drive_c")
        self.dl = mock.patch.object(plugfiles.paths, "DOWNLOADS", self.d / "dl"); self.dl.start()
    def tearDown(self):
        self.dl.stop(); self.tmp.cleanup()

    def zip(self, name, files: dict) -> Path:
        z = self.d / name
        with zipfile.ZipFile(z, "w") as f:
            for n, data in files.items(): f.writestr(n, data)
        return z

    def test_zip_with_every_format_and_other_systems(self):
        z = self.zip("Synth 1.2.zip", {
            "Synth/Windows/VST3/Synth.vst3/Contents/x86_64-win/Synth.vst3": VST3,
            "Synth/Windows/VST3/Synth.vst3/Contents/Resources/ui.png": b"png",
            "Synth/Windows/CLAP/Synth.clap": CLAP,
            "Synth/Windows/VST2/x64/Synth.dll": VST2,
            "Synth/Windows/VST2/x86/Synth.dll": VST2_32,
            "Synth/macOS/Synth.vst3/Contents/MacOS/Synth": b"\xcf\xfa\xed\xfe",
            "Synth/macOS/Synth.component/Contents/MacOS/Synth": b"x",
            "__MACOSX/Synth/._Synth.clap": b"junk",
            "Synth/Readme.pdf": b"%PDF"})
        self.assertTrue(plugfiles.handles(z))
        res = plugfiles.install(self.p, z, Reporter())
        got = {(f, str(t.relative_to(self.p.drive_c))) for f, t in res.installed}
        self.assertEqual(got, {
            ("VST3", "Program Files/Common Files/VST3/Synth.vst3"),
            ("CLAP", "Program Files/Common Files/CLAP/Synth.clap"),
            ("VST2", "Program Files/VstPlugins/Synth.dll")})
        self.assertTrue((self.p.drive_c / "Program Files/Common Files/VST3/Synth.vst3/Contents/Resources/ui.png").exists())
        self.assertEqual(pe.machine(self.p.drive_c / "Program Files/VstPlugins/Synth.dll"), "x64")
        why = dict(res.skipped)
        self.assertIn("macOS", why["Synth.vst3"]); self.assertIn("macOS", why["Synth.component"])
        self.assertNotIn("Synth.dll", why)          # its 32-bit twin is dropped quietly: the 64-bit one went in
        self.assertFalse((self.d / "dl/unpacked").exists() and any((self.d / "dl/unpacked").iterdir()))

    def test_32_bit_only_is_reported_not_copied(self):
        z = self.zip("Old.zip", {"Old.dll": VST2_32})
        res = plugfiles.install(self.p, z, Reporter())
        self.assertEqual(res.installed, []); self.assertIn("32-bit", dict(res.skipped)["Old.dll"])

    def test_vst2_with_its_own_files_keeps_a_folder(self):
        z = self.zip("Drums.zip", {"Drums/Drums.dll": VST2, "Drums/Drums.data": b"d", "Drums/Kits/a.wav": b"w",
                                   "Drums/helper.dll": HELPER, "Drums/Manual.pdf": b"%PDF", "Drums/Drums.vst3": VST3})
        res = plugfiles.install(self.p, z, Reporter())
        vst = self.p.drive_c / "Program Files/VstPlugins/Drums"
        self.assertIn(("VST2", vst / "Drums.dll"), res.installed)
        self.assertTrue((vst / "Drums.data").exists()); self.assertTrue((vst / "Kits/a.wav").exists())
        self.assertTrue((vst / "helper.dll").exists())
        self.assertFalse((vst / "Manual.pdf").exists())
        self.assertFalse((vst / "Drums.vst3").exists())       # the VST3 goes to its own folder, never twice
        self.assertIn(("VST3", self.p.drive_c / "Program Files/Common Files/VST3/Drums.vst3"), res.installed)

    def test_reinstall_replaces(self):
        z = self.zip("Fx.zip", {"Fx.clap": CLAP}); plugfiles.install(self.p, z, Reporter())
        t = self.p.drive_c / "Program Files/Common Files/CLAP/Fx.clap"; t.write_bytes(b"old")
        plugfiles.install(self.p, z, Reporter()); self.assertEqual(t.read_bytes(), CLAP)

    def test_archive_with_only_an_installer_hands_it_back(self):
        z = self.zip("Setup.zip", {"Plug Setup/setup.exe": make_pe(), "Plug Setup/unins000.exe": make_pe()})
        res = plugfiles.install(self.p, z, Reporter())
        self.assertEqual(res.installed, []); self.assertEqual([x.name for x in res.installers], ["setup.exe"])
        self.assertTrue(res.installers[0].exists())

    def test_loose_files(self):
        f = self.d / "Fx.vst3"; f.write_bytes(VST3)
        self.assertTrue(plugfiles.handles(f))
        res = plugfiles.install(self.p, f, Reporter())
        self.assertEqual(res.installed, [("VST3", self.p.drive_c / "Program Files/Common Files/VST3/Fx.vst3")])
        h = self.d / "helper.dll"; h.write_bytes(HELPER); self.assertFalse(plugfiles.handles(h))
        x = self.d / "setup.exe"; x.write_bytes(make_pe()); self.assertFalse(plugfiles.handles(x))

    def test_bundle_folder(self):
        b = self.d / "Pad.vst3/Contents/x86_64-win"; b.mkdir(parents=True); (b / "Pad.vst3").write_bytes(VST3)
        self.assertTrue(plugfiles.handles(self.d / "Pad.vst3"))
        res = plugfiles.install(self.p, self.d / "Pad.vst3", Reporter())
        self.assertTrue((self.p.drive_c / "Program Files/Common Files/VST3/Pad.vst3/Contents/x86_64-win/Pad.vst3").exists())

    def test_zip_slip_stays_inside(self):
        z = self.zip("Evil.zip", {"../../outside.clap": CLAP})
        plugfiles.install(self.p, z, Reporter())
        self.assertFalse((self.d / "outside.clap").exists())


if __name__ == "__main__": unittest.main()
