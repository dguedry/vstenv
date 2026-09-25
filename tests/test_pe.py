"""Reading version and icon out of Windows executables."""
import os, struct, unittest
from pathlib import Path
from vstenv import pe

NA_EXE = Path.home() / ".var/app/io.github.dguedry.nilinux/data/nilinux/prefix/drive_c/Program Files/Native Instruments/Native Access/Native Access.exe"

class PeTest(unittest.TestCase):
    def test_version_from_a_fixed_file_info_block(self):
        blob = b"\0" * 32 + "VS_VERSION_INFO".encode("utf-16-le") + b"\0\0" + struct.pack("<II", 0xFEEF04BD, 0x10000) \
               + struct.pack("<II", (3 << 16) | 26, (0 << 16) | 0) + b"\0" * 64
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".exe", delete=False) as f: f.write(blob); name = f.name
        try: self.assertEqual(pe.version(Path(name)), "3.26.0")
        finally: os.unlink(name)

    def test_garbage_is_not_an_icon(self):
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".exe", delete=False) as f: f.write(b"MZ" + b"\xff" * 300); name = f.name
        try: self.assertIsNone(pe.icon(Path(name))); self.assertIsNone(pe.version(Path(name)))
        finally: os.unlink(name)
        self.assertIsNone(pe.icon(Path("/nonexistent.exe")))

    @unittest.skipUnless(NA_EXE.exists(), "a real Windows executable is needed")
    def test_real_executable(self):
        self.assertRegex(pe.version(NA_EXE), r"^\d+\.\d+\.\d+")
        ico = pe.icon(NA_EXE)
        self.assertIsNotNone(ico)
        self.assertEqual(ico[:4], b"\x00\x00\x01\x00")            # ICONDIR: reserved 0, type 1
        count = struct.unpack_from("<H", ico, 4)[0]
        self.assertGreaterEqual(count, 1)
        w, h, colors, reserved, planes, bits, size, off = struct.unpack_from("<BBBBHHII", ico, 6)
        self.assertEqual(len(ico), 6 + 16 * count + sum(struct.unpack_from("<I", ico, 6 + 16 * i + 8)[0] for i in range(count)))

if __name__ == "__main__": unittest.main()
