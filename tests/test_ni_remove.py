import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from vstenv.vendors.ni import products as pr


class FakePrefix:
    REG64 = "reg"
    def __init__(self, root):
        self.drive_c = Path(root) / "drive_c"; self.public_docs = self.drive_c / "users/Public/Documents"; self.ran = []
    def to_host(self, w): return self.drive_c / w[3:].replace("\\", "/")
    def run(self, argv, **kw): self.ran.append(argv)


class RemoveTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.p = FakePrefix(self.tmp.name); c = self.p.drive_c
        for d in ("Program Files/Native Instruments/Kontakt 8", "Program Files/Common Files/Native Instruments/Kontakt 8",
                  "Program Files/Common Files/VST3/Kontakt 8.vst3/Contents/x86_64-win", "Program Files/Common Files/VST3/Other.vst3",
                  "users/Public/Documents/Alicias Keys Library", "Program Files/Native Instruments/VSTPlugins 64 bit"):
            (c / d).mkdir(parents=True)
        (c / "Program Files/Native Instruments/VSTPlugins 64 bit/Kontakt 8.dll").write_bytes(b"MZ")
        (c / "Program Files/Native Instruments/VSTPlugins 64 bit/Kontakt 8 FX.dll").write_bytes(b"MZ")
        (c / "Program Files/Native Instruments/VSTPlugins 64 bit/Kontakt 80s Keys.dll").write_bytes(b"MZ")   # another product
        rec = pr.installed_products_dir(self.p); rec.mkdir(parents=True)
        (rec / "Kontakt 8.json").write_text(json.dumps({"ContentDir": "C:\\Program Files\\Common Files\\Native Instruments\\Kontakt 8",
                                                        "InstallDir": "C:\\Program Files\\Native Instruments\\Kontakt 8"}))
        (rec / "Alicias Keys.json").write_text(json.dumps({"ContentDir": "C:\\users\\Public\\Documents\\Alicias Keys Library\\"}))
        # a library put straight into a shared root must not take the root with it
        (rec / "Shared.json").write_text(json.dumps({"ContentDir": "C:\\users\\Public\\Documents"}))
        self.patches = [mock.patch.object(pr, "hints", return_value={}),
                        mock.patch.object(pr, "hive_keys", return_value={"Kontakt 8": {"InstallDir": "C:\\Program Files\\Native Instruments\\Kontakt 8"}})]
        for x in self.patches: x.start()
    def tearDown(self):
        for x in self.patches: x.stop()
        self.tmp.cleanup()

    def test_kontakt(self):
        c = self.p.drive_c
        plan = pr.remove(self.p, "Kontakt 8")
        self.assertFalse((c / "Program Files/Native Instruments/Kontakt 8").exists())
        self.assertFalse((c / "Program Files/Common Files/Native Instruments/Kontakt 8").exists())
        self.assertFalse((c / "Program Files/Common Files/VST3/Kontakt 8.vst3").exists())
        self.assertFalse((c / "Program Files/Native Instruments/VSTPlugins 64 bit/Kontakt 8.dll").exists())
        self.assertFalse((c / "Program Files/Native Instruments/VSTPlugins 64 bit/Kontakt 8 FX.dll").exists())
        self.assertTrue((c / "Program Files/Native Instruments/VSTPlugins 64 bit/Kontakt 80s Keys.dll").exists())
        self.assertTrue((c / "Program Files/Common Files/VST3/Other.vst3").exists())
        self.assertTrue((c / "Program Files/Common Files/Native Instruments").exists())
        self.assertFalse((pr.installed_products_dir(self.p) / "Kontakt 8.json").exists())
        self.assertIn(["reg", "delete", pr.NI_KEY + "\\Kontakt 8", "/f"], self.p.ran)

    def test_library_and_shared_root(self):
        pr.remove(self.p, "Alicias Keys")
        self.assertFalse((self.p.drive_c / "users/Public/Documents/Alicias Keys Library").exists())
        self.assertEqual(pr.removal_plan(self.p, "Shared")["dirs"], [])
        pr.remove(self.p, "Shared")
        self.assertTrue(self.p.public_docs.exists())


if __name__ == "__main__": unittest.main()
