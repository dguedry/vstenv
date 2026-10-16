import struct
import tempfile
import unittest
from pathlib import Path

from vstenv.installers import iaroots


def pe(bits):
    """A minimal PE header: MZ, e_lfanew -> 'PE\\0\\0' + machine."""
    machine = 0x8664 if bits == 64 else 0x14c
    head = bytearray(b"MZ" + b"\0" * 0x3e); struct.pack_into("<I", head, 0x3c, 0x40)
    return bytes(head) + b"PE\0\0" + struct.pack("<H", machine) + b"\0" * 20


class FakeMsi:
    """The Absynth 5 shape: roots under TARGETDIR named P<hash>_1 with DefaultDir '.',
    files reached through Component -> Directory, sources under OFFLINE/<hash>/..."""
    def __init__(self):
        self.dirs = [("TARGETDIR", "", "SourceDir")]
        self.comps, self.files, self.reg = [], [], []
    def root(self, h): self.dirs.append((f"P{h}_1", "TARGETDIR", ".")); return f"P{h}_1"
    def sub(self, parent, name, src=""):
        """A target subfolder: the real rows read 'Short~1|Name:.' (target Name,
        source '.'); the OFFLINE source rows read '.:OFFLIN~1|OFFLINE'."""
        d = f"{parent}5c{name}"; self.dirs.append((d, parent, f"{name}:{src or '.'}")); return d
    def file(self, d, name): c = f"c{len(self.comps)}"; self.comps.append((c, d)); self.files.append((f"f{len(self.files)}", c, name))
    def rows(self, table):
        if table == "Directory": return [{"Directory": a, "Directory_Parent": b, "DefaultDir": c} for a, b, c in self.dirs]
        if table == "Component": return [{"Component": a, "Directory_": b} for a, b in self.comps]
        if table == "File": return [{"File": a, "Component_": b, "FileName": c} for a, b, c in self.files]
        if table == "Registry": return self.reg
        return []


class InferRootsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.bag = Path(self.tmp.name)
        m = FakeMsi()
        # Common Files\Native Instruments: product folder, Kore 2, Service Center
        c = m.root("BA7B8D2"); m.file(m.sub(c, "Absynth 5", ""), "Bass.glo"); m.file(m.sub(c, "Kore 2", ""), "k.dat"); m.file(m.sub(c, "Service Center", ""), "Absynth 5.xml")
        # app folders: 64-bit and 32-bit exe
        a64 = m.root("20D34BD9"); m.file(a64, "Absynth 5.exe"); self._put("20D34BD9", "Absynth 5.exe", pe(64))
        a32 = m.root("86EB1372"); m.file(a32, "Absynth 5.exe"); self._put("86EB1372", "Absynth 5.exe", pe(32))
        # VST folders
        v64 = m.root("F4320BB3"); m.file(v64, "Absynth 5 FX.dll"); self._put("F4320BB3", "Absynth 5 FX.dll", pe(64))
        v32 = m.root("C8A37527"); m.file(v32, "Absynth 5 FX.dll"); self._put("C8A37527", "Absynth 5 FX.dll", pe(32))
        # docs only
        d = m.root("24612F5F"); m.file(m.sub(d, "Documentation", ""), "EULA.rtf")
        # AAX
        x = m.root("C9B0B395"); m.file(m.sub(m.sub(x, "Contents", ""), "x64", ""), "Absynth 5.aaxplugin")
        key = "SOFTWARE\\Native Instruments\\Absynth 5"
        m.reg = [{"Root": 2, "Key": key, "Name": "InstallDir", "Value": "[PF3B9F29_3]"},
                 {"Root": 2, "Key": key, "Name": "InstallVST64Dir", "Value": "[P96A24EAC_3]"},
                 {"Root": 2, "Key": key, "Name": "InstallVSTDir", "Value": "[PB726CBFD_3]"},
                 {"Root": 2, "Key": key, "Name": "InstallAAX64Dir", "Value": "[P9469248_3]"},
                 {"Root": 2, "Key": key, "Name": "ContentDir", "Value": "[P2F0D26C_3]"},
                 {"Root": 2, "Key": key, "Name": "ContentVersion", "Value": "5.3.4"},
                 {"Root": 1, "Key": "Software\\Other", "Name": "x", "Value": "[P1_3]"}]
        self.m = m

    def _put(self, h, name, data):
        d = self.bag / "OFFLINE" / h / "DEADBEEF"; d.mkdir(parents=True, exist_ok=True); (d / name).write_bytes(data)

    def test_roots_follow_content_and_bitness(self):
        roots, regkeys, notes = iaroots.infer_roots(self.m, self.bag, "Absynth 5")
        self.assertEqual(roots["PBA7B8D2_1"], "C:\\Program Files\\Common Files\\Native Instruments")
        self.assertEqual(roots["P20D34BD9_1"], "C:\\Program Files\\Native Instruments\\Absynth 5")
        self.assertEqual(roots["P86EB1372_1"], "C:\\Program Files (x86)\\Native Instruments\\Absynth 5")
        self.assertEqual(roots["PF4320BB3_1"], "C:\\Program Files\\Native Instruments\\VSTPlugins 64 bit")
        self.assertEqual(roots["PC8A37527_1"], "C:\\Program Files (x86)\\Native Instruments\\VSTPlugins 32 bit")
        self.assertEqual(roots["P24612F5F_1"], "C:\\Program Files\\Native Instruments\\Absynth 5")

    def test_aax_is_left_out(self):
        roots, _, notes = iaroots.infer_roots(self.m, self.bag, "Absynth 5")
        self.assertNotIn("PC9B0B395_1", roots)
        self.assertTrue(any("AAX" in n for n in notes))

    def test_registry_values_are_filled_by_name(self):
        _, regkeys, _ = iaroots.infer_roots(self.m, self.bag, "Absynth 5")
        k = "SOFTWARE\\Native Instruments\\Absynth 5\\"
        self.assertEqual(regkeys[k + "InstallDir"], "C:\\Program Files\\Native Instruments\\Absynth 5\\")
        self.assertEqual(regkeys[k + "InstallVST64Dir"], "C:\\Program Files\\Native Instruments\\VSTPlugins 64 bit\\")
        self.assertEqual(regkeys[k + "InstallVSTDir"], "C:\\Program Files (x86)\\Native Instruments\\VSTPlugins 32 bit\\")
        self.assertEqual(regkeys[k + "ContentDir"], "C:\\Program Files\\Common Files\\Native Instruments\\Absynth 5\\")
        self.assertEqual(regkeys[k + "ContentVersion"], "5.3.4")
        self.assertNotIn(k + "InstallAAX64Dir", regkeys, "no AAX folder was deployed, so no path to register")
        self.assertFalse(any("Other" in x for x in regkeys), "only HKLM Native Instruments keys")

    def test_pe_bits(self):
        f = self.bag / "x.exe"; f.write_bytes(pe(64)); self.assertEqual(iaroots.pe_bits(f), 64)
        f.write_bytes(pe(32)); self.assertEqual(iaroots.pe_bits(f), 32)
        f.write_bytes(b"not a pe"); self.assertIsNone(iaroots.pe_bits(f))

    def test_rescue_falls_back_to_inference(self):
        src = Path("vstenv/installers/installaware.py").read_text()
        self.assertEqual(src.count("_infer("), 3, "defined once, used by both the staged finish and install()")
        self.assertNotIn('raise RuntimeError("trace has no destination roots; cannot deploy")', src)
