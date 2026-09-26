import unittest
from pathlib import Path
from vstenv import pe

class ImportsTest(unittest.TestCase):
    def test_non_pe_files_have_no_imports(self):
        self.assertEqual(pe.imports(Path("/etc/hostname")), []); self.assertEqual(pe.imports(Path("/nonexistent")), [])
    @unittest.skipUnless((Path.home() / ".local/share/vstenv/prefix/drive_c/Program Files/Steinberg/HALion Sonic/graphics2d.dll").exists(), "needs HALion's graphics2d.dll")
    def test_steinberg_graphics2d_imports_dcomp(self):
        imps = pe.imports(Path.home() / ".local/share/vstenv/prefix/drive_c/Program Files/Steinberg/HALion Sonic/graphics2d.dll")
        self.assertIn("dcomp.dll", imps); self.assertIn("d2d1.dll", imps)
    @unittest.skipUnless((Path.home() / ".local/share/vstenv/prefix/drive_c/Program Files/Native Instruments/Kontakt 8/Kontakt 8.exe").exists(), "needs Kontakt")
    def test_kontakt_does_not(self):
        self.assertNotIn("dcomp.dll", pe.imports(Path.home() / ".local/share/vstenv/prefix/drive_c/Program Files/Native Instruments/Kontakt 8/Kontakt 8.exe"))

if __name__ == "__main__": unittest.main()
