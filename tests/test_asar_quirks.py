import json, struct, tempfile, unittest
from pathlib import Path
from vstenv import asar, quirks
from vstenv.vendors import ik

def make_asar(files: dict[str, bytes]) -> bytes:
    header = {"files": {}}; blobs = b""; off = 0
    for path, blob in files.items():
        node = header
        parts = path.split("/")
        for part in parts[:-1]: node = node["files"].setdefault(part, {"files": {}})
        node["files"][parts[-1]] = {"offset": str(off), "size": len(blob), "integrity": asar._integrity(blob)}
        blobs += blob; off += len(blob)
    hj = json.dumps(header, separators=(",", ":")).encode(); pad = ((len(hj) + 3) & ~3) - len(hj)
    return struct.pack("<IIII", 4, len(hj) + pad + 8, len(hj) + pad + 4, len(hj)) + hj + b"\0" * pad + blobs

class AsarTest(unittest.TestCase):
    def test_patch_and_read(self):
        with tempfile.TemporaryDirectory() as d:
            a = Path(d) / "app.asar"; a.write_bytes(make_asar({"main.js": b"hello", "local_modules/os-info/index.js": b"x = 1;", "big.bin": b"z" * 10}))
            res = asar.patch(a, {r"local_modules/os-info/index\.js": lambda b: b.replace(b"1", b"2"), r"main\.js": lambda b: None})
            self.assertEqual(res[r"local_modules/os-info/index\.js"], "patched"); self.assertEqual(res[r"main\.js"], "unchanged")
            self.assertEqual(asar.read(a, "local_modules/os-info/index.js"), b"x = 2;")
            self.assertEqual(asar.read(a, "main.js"), b"hello"); self.assertEqual(asar.read(a, "big.bin"), b"z" * 10)
            self.assertTrue(a.with_suffix(".asar.orig").exists())
            hdr = asar._header(a.read_bytes())[0]
            node = hdr["files"]["local_modules"]["files"]["os-info"]["files"]["index.js"]
            self.assertEqual(node["integrity"]["hash"], asar._integrity(b"x = 2;")["hash"])

class QuirkTest(unittest.TestCase):
    def test_ik_os_info(self):
        js = b"const winVersion = execSync('ver').toString().trim()\n        version = winVersion.match(/\\[([^\\]]+)\\]/)\n        for (x of y) {}"
        out = ik.os_info_edit(js)
        self.assertIn(b"|| winVersion.match(/(\\d+\\.\\d+[\\d.]*)/)", out); self.assertIsNone(ik.os_info_edit(out))
        with self.assertRaises(LookupError): ik.os_info_edit(b"nothing here")
    def test_electron_disable_gpu_inserted_after_the_electron_require(self):
        js = b"// eslint-disable-next-line\nconst { app, dialog } = require('electron')\n\nconst fs = require('fs')\napp.on('ready', () => {})\n"
        out = quirks.electron_disable_gpu(js)
        lines = out.split(b"\n")
        self.assertIn(b"require('electron')", lines[1]); self.assertTrue(lines[2].startswith(b"require('electron').app.disableHardwareAcceleration()"))
        self.assertIn(quirks.MARK, lines[2]); self.assertEqual(lines[3:], js.split(b"\n")[2:])
        self.assertIsNone(quirks.electron_disable_gpu(out))                       # idempotent
        with self.assertRaises(LookupError): quirks.electron_disable_gpu(b"const x = require('fs')\n")

    def test_launch_args_for_electron_apps(self):
        with tempfile.TemporaryDirectory() as d:
            from vstenv.wine import Prefix, WineBuild
            p = Prefix(Path(d), WineBuild(Path("/w")))
            app = p.drive_c / "Program Files/X"; (app / "resources").mkdir(parents=True); (app / "resources/app.asar").write_bytes(b"")
            plain = p.drive_c / "Program Files/Y"; plain.mkdir(parents=True)
            self.assertEqual(quirks.launch_args(p, "X", r"C:\Program Files\X"), ["--disable-gpu"])
            self.assertEqual(quirks.launch_args(p, "Y", r"C:\Program Files\Y"), [])
            quirks.LAUNCH_ARGS["X"] = []
            try: self.assertEqual(quirks.launch_args(p, "X", r"C:\Program Files\X"), [])
            finally: quirks.LAUNCH_ARGS.pop("X")
    def test_match_by_name(self):
        self.assertTrue(quirks._match("IK Product Manager")); self.assertFalse(quirks._match("Kontakt 8"))

if __name__ == "__main__": unittest.main()
