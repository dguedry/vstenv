"""Per-plugin yabridge.toml options (Troubleshoot's "drop files onto the plugin
in REAPER" = editor_force_dnd): vstenv owns one marked block per file and never
touches what the person wrote."""
import json, tempfile, unittest
from pathlib import Path
from unittest import mock

from vstenv import recipes, yabridge
from tests.test_recipes import FakePrefix


class YabridgeConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.home = Path(self.tmp.name)
        self.patch = mock.patch.object(Path, "home", return_value=self.home); self.patch.start()
    def tearDown(self): self.patch.stop(); self.tmp.cleanup()

    def test_each_format_goes_to_its_own_root_with_linux_names(self):
        on = {"editor_force_dnd": True}
        yabridge.write_config({"KORG/TRITON.dll": on, "Kontakt 8.vst3": on, "Pro-Q [4]*.clap": on, "notes.txt": on})
        self.assertEqual((self.home / ".vst/yabridge/yabridge.toml").read_text(),
                         f'{yabridge.CONFIG_BEGIN}\n["KORG/TRITON.so"]\neditor_force_dnd = true\n\n{yabridge.CONFIG_END}\n')
        self.assertIn('["Kontakt 8.vst3"]', (self.home / ".vst3/yabridge/yabridge.toml").read_text())
        self.assertIn('["Pro-Q [[]4[]][*].clap"]', (self.home / ".clap/yabridge/yabridge.toml").read_text())

    def test_the_persons_own_sections_are_kept_and_win(self):
        f = self.home / ".vst3/yabridge/yabridge.toml"; f.parent.mkdir(parents=True)
        f.write_text('["Mine.vst3"]\nframe_rate = 24\n')
        yabridge.write_config({"Kontakt 8.vst3": {"editor_force_dnd": True}})
        yabridge.write_config({"Kontakt 8.vst3": {"editor_force_dnd": True}})   # idempotent
        text = f.read_text()
        self.assertTrue(text.startswith('["Mine.vst3"]\nframe_rate = 24\n\n' + yabridge.CONFIG_BEGIN))
        self.assertEqual(text.count(yabridge.CONFIG_BEGIN), 1)
        yabridge.write_config({})
        self.assertEqual(f.read_text(), '["Mine.vst3"]\nframe_rate = 24\n')

    def test_a_file_that_was_only_ours_is_removed(self):
        yabridge.write_config({"Kontakt 8.vst3": {"editor_force_dnd": True}})
        self.assertEqual(yabridge.write_config({}), [self.home / ".vst3/yabridge/yabridge.toml"])
        self.assertFalse((self.home / ".vst3/yabridge/yabridge.toml").exists())

    def test_the_knob_maps_to_editor_force_dnd(self):
        p = FakePrefix(self.home / "pfx")
        f = self.home / "fetched.json"; f.write_text(json.dumps({"plugins": {"Tabled.vst3": {"force_dnd": "on"}}}))
        with mock.patch.object(recipes, "FETCHED", f):
            recipes.set_choice(p, "plugins", "Kontakt 8.vst3", force_dnd="on")
            recipes.set_choice(p, "plugins", "Tabled.vst3", force_dnd="off")
            self.assertEqual(recipes.plugin_yabridge_options(p), {"Kontakt 8.vst3": {"editor_force_dnd": True}})


if __name__ == "__main__": unittest.main()
