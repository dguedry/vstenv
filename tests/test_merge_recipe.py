"""The approval workflow's merge step: the app's share text goes into the
table as is, confirmations accumulate, junk is refused."""
import importlib.util, json, tempfile, unittest
from pathlib import Path
from unittest import mock

from vstenv import recipes

spec = importlib.util.spec_from_file_location("merge_recipe", Path("scripts/merge-recipe.py"))
mr = importlib.util.module_from_spec(spec); spec.loader.exec_module(mr)


class MergeRecipeTest(unittest.TestCase):
    def _share_body(self, kind, name, settings, symptom):
        with mock.patch.object(recipes, "environment", return_value={"vstenv": "0.1.0", "wine": "w", "dxvk": "3.1", "gpu": "GPU A", "session": "x11 / X-Cinnamon", "distribution": "d"}):
            return recipes.share_text(None, kind, name, settings, symptom)[1]

    def test_the_apps_share_text_merges_as_is(self):
        body = self._share_body("programs", "Thing.exe", {"rendering": "wine"}, "blank window")
        new = mr.extract(body)
        self.assertEqual(new["programs"]["Thing.exe"]["rendering"], "wine")
        table = {"version": 1, "programs": {}, "plugins": {}}
        lines = mr.merge(table, new)
        self.assertEqual(lines, ["program Thing.exe: rendering=wine"])
        self.assertEqual(table["programs"]["Thing.exe"]["why"], "blank window")
        self.assertEqual(table["programs"]["Thing.exe"]["confirmed"], ["GPU A; x11 / X-Cinnamon; vstenv 0.1.0"])

    def test_a_second_report_adds_a_confirmation_and_keeps_the_newer_settings(self):
        table = {"programs": {"Thing.exe": {"rendering": "wine", "why": "first", "confirmed": ["GPU A; x11; vstenv 0.1.0"]}}, "plugins": {}}
        new = {"programs": {"Thing.exe": {"rendering": "gdi", "why": "second", "confirmed": ["GPU B; wayland; vstenv 0.1.1"]}}, "plugins": {}}
        mr.merge(table, new)
        e = table["programs"]["Thing.exe"]
        self.assertEqual(e["rendering"], "gdi"); self.assertEqual(e["why"], "second")
        self.assertEqual(e["confirmed"], ["GPU A; x11; vstenv 0.1.0", "GPU B; wayland; vstenv 0.1.1"])
        mr.merge(table, new)                                    # the same report twice adds nothing
        self.assertEqual(len(table["programs"]["Thing.exe"]["confirmed"]), 2)

    def test_junk_is_refused_and_unknown_settings_dropped(self):
        self.assertFalse(any(mr.extract("no block here")[k] for k in recipes.KINDS))
        self.assertFalse(any(mr.extract("```json\n{not json}\n```")[k] for k in recipes.KINDS))
        new = mr.extract('```json\n{"programs": {"X.exe": {"rendering": "wine", "command": "rm -rf /"}}, "weird": 1}\n```')
        self.assertEqual(new["programs"]["X.exe"], {"rendering": "wine"})

    def test_main_writes_the_file_and_signals_nothing_usable(self):
        with tempfile.TemporaryDirectory() as tmp:
            table = Path(tmp) / "recipes.json"; table.write_text(json.dumps({"version": 1, "programs": {}, "plugins": {}}))
            body = Path(tmp) / "body.md"; body.write_text(self._share_body("plugins", "S.vst3", {"rendering": "gdi"}, "vanishes"))
            self.assertEqual(mr.main(["x", str(body), str(table)]), 0)
            self.assertEqual(json.loads(table.read_text())["plugins"]["S.vst3"]["rendering"], "gdi")
            self.assertTrue(recipes._clean(json.loads(table.read_text()))["plugins"])   # the app loads what was written
            body.write_text("nothing")
            self.assertEqual(mr.main(["x", str(body), str(table)]), 2)

    def test_workflow_only_fires_on_approved_recipe_issues_and_skips_release(self):
        f = Path(".github/workflows/recipe.yml")
        if not f.exists(): self.skipTest("the approval workflow is not installed in this checkout")
        wf = f.read_text()
        self.assertIn("github.event.label.name == 'approved'", wf)
        self.assertIn("contains(github.event.issue.labels.*.name, 'recipe')", wf)
        self.assertIn("[no-release]", wf)
        self.assertIn("BODY: ${{ github.event.issue.body }}", wf)         # the body goes through env, never into the script
        self.assertIn("scripts/merge-recipe.py", wf)


if __name__ == "__main__":
    unittest.main()
