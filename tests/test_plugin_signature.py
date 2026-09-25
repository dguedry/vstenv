"""New plugins are bridged while Native Access is still open.

Syncing only when Native Access exits is not enough: people install a product
and leave it running, then find their DAW cannot see the new instrument. The
plugin is installed, just not bridged, which looks exactly like a failed
install. The GUI polls this signature while Native Access runs.
"""
import tempfile, unittest
from pathlib import Path

from vstenv import yabridge
from vstenv.wine import Prefix, WineBuild


class PluginSignature(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "wine/bin").mkdir(parents=True)
        (root / "wine/bin/wine").write_text("#!/bin/sh\n")
        (root / "wine/bin/wine").chmod(0o755)
        self.p = Prefix(root / "prefix", WineBuild(root / "wine"))
        self.vst3 = self.p.drive_c / "Program Files/Common Files/VST3"
        self.vst3.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_empty_prefix_has_no_plugins(self):
        self.assertEqual(yabridge.plugin_signature(self.p), frozenset())

    def test_missing_program_files_is_not_an_error(self):
        p = Prefix(Path(self.tmp.name) / "nothing", WineBuild(Path(self.tmp.name) / "wine"))
        self.assertEqual(yabridge.plugin_signature(p), frozenset())

    def test_a_new_plugin_is_detected(self):
        before = yabridge.plugin_signature(self.p)
        (self.vst3 / "Battery 4.vst3").write_bytes(b"x" * 2048)
        after = yabridge.plugin_signature(self.p)
        self.assertEqual(len(after - before), 1)
        self.assertIn("Battery 4.vst3", next(iter(after - before))[0])

    def test_a_plugin_that_grows_is_detected(self):
        """A product that installs in stages must not be bridged half-written."""
        f = self.vst3 / "Kontakt 8.vst3"
        f.write_bytes(b"x" * 1024)
        first = yabridge.plugin_signature(self.p)
        f.write_bytes(b"x" * 4096)
        self.assertNotEqual(yabridge.plugin_signature(self.p), first)

    def test_unchanged_prefix_gives_the_same_answer(self):
        (self.vst3 / "Dirt.vst3").write_bytes(b"x" * 512)
        self.assertEqual(yabridge.plugin_signature(self.p), yabridge.plugin_signature(self.p))

    def test_non_plugin_files_are_ignored(self):
        (self.vst3 / "readme.txt").write_text("not a plugin")
        (self.vst3 / "installer.log").write_text("nor this")
        self.assertEqual(yabridge.plugin_signature(self.p), frozenset())

    def test_32_bit_program_files_is_ignored(self):
        """Those are 32-bit builds this prefix does not bridge."""
        x86 = self.p.drive_c / "Program Files (x86)/Common Files/VST3"
        x86.mkdir(parents=True)
        (x86 / "Old Plugin.vst3").write_bytes(b"x" * 256)
        self.assertEqual(yabridge.plugin_signature(self.p), frozenset())

    def test_deeply_buried_files_are_ignored(self):
        """Matches the depth limit plugin_dirs() uses, so the two agree."""
        deep = self.p.drive_c / "Program Files/a/b/c/d/e/f/g"
        deep.mkdir(parents=True)
        (deep / "Buried.vst3").write_bytes(b"x" * 256)
        self.assertEqual(yabridge.plugin_signature(self.p), frozenset())


if __name__ == "__main__":
    unittest.main()
