"""fontalias: a TrueType face renamed in place, structurally intact."""
import struct, unittest
from pathlib import Path
from vstenv import fontalias

CANDIDATES = [Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"), Path("/usr/share/fonts/dejavu/DejaVuSans.ttf"),
              Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"), Path("/usr/share/fonts/liberation-sans/LiberationSans-Regular.ttf")]

def _tables(ttf: bytes) -> dict:
    n = struct.unpack_from(">H", ttf, 4)[0]
    return {tag: (csum, off, length) for tag, csum, off, length in (struct.unpack_from(">4sIII", ttf, 12 + 16 * i) for i in range(n))}

class FontAliasTest(unittest.TestCase):
    def setUp(self):
        self.src = next((c for c in CANDIDATES if c.exists()), None)
        if self.src is None: self.skipTest("no TrueType font on this host to rename")
    def test_rename_changes_only_the_names(self):
        ttf = self.src.read_bytes()
        out = fontalias.rename(ttf, "Segoe UI Semibold", "Regular", "SegoeUI-Semibold", typographic_family="Segoe UI", typographic_style="Semibold")
        names = fontalias.read_names(out)
        self.assertEqual(names[1], "Segoe UI Semibold"); self.assertEqual(names[2], "Regular"); self.assertEqual(names[4], "Segoe UI Semibold")
        self.assertEqual(names[6], "SegoeUI-Semibold"); self.assertEqual(names[16], "Segoe UI"); self.assertEqual(names[17], "Semibold")
        self.assertIn("Selawik", names[10])
        old, new = _tables(ttf), _tables(out)
        self.assertEqual(set(old), set(new), "every table survives")
        for tag in old:
            if tag in (b"name", b"head"): continue
            (_, o1, l1), (_, o2, l2) = old[tag], new[tag]
            self.assertEqual(ttf[o1:o1 + l1], out[o2:o2 + l2], f"{tag} unchanged")
            self.assertEqual(o2 % 4, 0, "tables stay 4-byte aligned")
            self.assertEqual(new[tag][0], fontalias._checksum(out[o2:o2 + l2]), f"{tag} checksum")
        # the whole-file checksum adjustment in head makes the file sum to the magic value
        self.assertEqual(fontalias._checksum(out), 0xB1B0AFBA)
    def test_read_names_of_the_original(self):
        names = fontalias.read_names(self.src.read_bytes())
        self.assertIn(1, names); self.assertTrue(names[1])

if __name__ == "__main__": unittest.main()
