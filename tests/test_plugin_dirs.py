"""plugin_dirs must not register a directory nested under one it already lists:
yabridge scans recursively, so a VST3 subfolder (Common Files/VST3/Soundtoys)
under the registered VST3 root would bridge its plugins twice and a DAW would
show each one doubled."""
import tempfile, unittest
from pathlib import Path
from unittest import mock
from vstenv import yabridge, vendors
from vstenv.wine import Prefix, WineBuild


class PluginDirsNestingTest(unittest.TestCase):
    def _prefix(self, tmp):
        p = Prefix(Path(tmp) / "prefix", WineBuild(Path(tmp) / "wine"))
        (p.drive_c).mkdir(parents=True, exist_ok=True)
        return p

    def test_subdir_of_a_registered_root_is_not_added(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = self._prefix(tmp)
            vst3 = p.drive_c / "Program Files/Common Files/VST3"
            # a plugin inside a vendor subfolder of the VST3 root
            (vst3 / "Soundtoys/Tremolator.vst3").mkdir(parents=True)
            (vst3 / "Steinberg/HALion.vst3").mkdir(parents=True)
            # a vendor module that (wrongly) also declares the subfolder
            fake = mock.Mock(); fake.plugin_dirs.return_value = ["Program Files/Common Files/VST3/Soundtoys"]
            with mock.patch.object(vendors, "all", return_value=[fake]):
                dirs = [str(d.relative_to(p.drive_c)) for d in yabridge.plugin_dirs(p)]
            self.assertIn("Program Files/Common Files/VST3", dirs)
            self.assertNotIn("Program Files/Common Files/VST3/Soundtoys", dirs)
            self.assertNotIn("Program Files/Common Files/VST3/Steinberg", dirs)

    def test_a_registered_root_added_after_its_child_replaces_the_child(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = self._prefix(tmp)
            vst3 = p.drive_c / "Program Files/Common Files/VST3"
            (vst3 / "Soundtoys/Tremolator.vst3").mkdir(parents=True)
            # order: child first (from discovery), then parent (STANDARD_DIRS) -- parent must win
            with mock.patch.object(yabridge, "STANDARD_DIRS", ["Program Files/Common Files/VST3"]), \
                 mock.patch.object(vendors, "all", return_value=[]):
                dirs = [str(d.relative_to(p.drive_c)) for d in yabridge.plugin_dirs(p)]
            vst3_dirs = [d for d in dirs if "Common Files/VST3" in d]
            self.assertEqual(vst3_dirs, ["Program Files/Common Files/VST3"])   # exactly one, the root


if __name__ == "__main__": unittest.main()
