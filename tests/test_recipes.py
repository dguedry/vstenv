"""Recipes: a fixed menu of settings per program or plugin, the user's choice
over a table over detection, applied through the registry and the host
launcher's override file, and shareable as a prefilled issue."""
import json, tempfile, unittest
from pathlib import Path
from unittest import mock

from vstenv import recipes


class FakePrefix:
    """The registry surface recipes.py uses, plus a drive_c for its files."""
    def __init__(self, root):
        self.path = Path(root); self.drive_c = self.path / "drive_c"; self.drive_c.mkdir(parents=True, exist_ok=True)
        self.values = {}; self.build = None
    def reg_query(self, key): return dict(self.values.get(key, {}))
    def reg_add(self, key, name, value, kind="REG_SZ"): self.values.setdefault(key, {})[name] = value
    def reg_delete_value(self, key, name): self.values.get(key, {}).pop(name, None)
    def run(self, *a, **k): return mock.Mock(returncode=0)


def _table(tmp, data):
    f = Path(tmp) / "fetched.json"; f.write_text(json.dumps(data)); return f


class TableTest(unittest.TestCase):
    def test_only_known_kinds_knobs_and_values_survive(self):
        raw = {"programs": {"X.exe": {"rendering": "wine", "wm_away": "maybe", "command": "rm -rf", "why": "blank window"},
                            "Y.exe": {"rendering": "auto"}, "Z.exe": "wine"},
               "plugins": {"S.vst3": {"rendering": "gdi", "wm_away": "on"}}, "other": {"a": {}}}
        t = recipes._clean(raw)
        self.assertEqual(t["programs"], {"X.exe": {"rendering": "wine", "why": "blank window"}})
        self.assertEqual(t["plugins"], {"S.vst3": {"rendering": "gdi"}})
        self.assertEqual(set(t), set(recipes.KINDS))
        self.assertEqual(recipes._clean("junk")["programs"], {})

    def test_shipped_table_is_valid_and_fetched_overlays_it(self):
        self.assertTrue(recipes.SHIPPED.exists())
        with tempfile.TemporaryDirectory() as tmp:
            f = _table(tmp, {"programs": {"New.exe": {"rendering": "gdi"}}})
            with mock.patch.object(recipes, "FETCHED", f):
                self.assertEqual(recipes.table()["programs"]["New.exe"], {"rendering": "gdi"})
            with mock.patch.object(recipes, "FETCHED", Path(tmp) / "missing.json"):
                self.assertNotIn("New.exe", recipes.table()["programs"])

    def test_update_keeps_only_a_valid_table_and_fails_quietly(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "dl" / "recipes.json"
            with mock.patch.object(recipes, "FETCHED", f), \
                 mock.patch("vstenv.download.text", return_value=json.dumps({"programs": {"A.exe": {"rendering": "wine", "evil": 1}}})):
                self.assertTrue(recipes.update())
            self.assertEqual(json.loads(f.read_text())["programs"], {"A.exe": {"rendering": "wine"}})
            with mock.patch.object(recipes, "FETCHED", f), mock.patch("vstenv.download.text", side_effect=OSError("offline")):
                self.assertFalse(recipes.update())
            self.assertTrue(f.exists())            # the earlier copy stays


class ChoicesTest(unittest.TestCase):
    def test_choices_round_trip_and_auto_forgets(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = FakePrefix(tmp)
            self.assertEqual(recipes.set_choice(p, "programs", "X.exe", rendering="wine", wm_away="on"), {"rendering": "wine", "wm_away": "on"})
            self.assertEqual(recipes.choices(p)["programs"]["X.exe"], {"rendering": "wine", "wm_away": "on"})
            self.assertEqual(recipes.set_choice(p, "programs", "X.exe", rendering="auto"), {"wm_away": "on"})
            self.assertEqual(recipes.set_choice(p, "programs", "X.exe", wm_away="auto"), {})
            self.assertNotIn("X.exe", recipes.choices(p)["programs"])
            with self.assertRaises(ValueError): recipes.set_choice(p, "programs", "X.exe", rendering="fast")
            with self.assertRaises(ValueError): recipes.set_choice(p, "plugins", "S.vst3", wm_away="on")
            with self.assertRaises(ValueError): recipes.set_choice(p, "things", "S.vst3", rendering="wine")

    def test_effective_is_choice_over_table_over_auto(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = FakePrefix(tmp)
            f = _table(tmp, {"programs": {"X.exe": {"rendering": "gdi", "wm_away": "on"}}})
            with mock.patch.object(recipes, "FETCHED", f):
                recipes.set_choice(p, "programs", "X.exe", rendering="wine")
                e = recipes.effective(p, "programs", "X.exe")
                self.assertEqual(e["rendering"], ("wine", "you"))
                self.assertEqual(e["wm_away"], ("on", "recipe"))
                self.assertEqual(e["browser_software_gl"], ("auto", "auto"))
                self.assertTrue(recipes.pinned(p, "programs", "X.exe", "rendering"))
                self.assertTrue(recipes.pinned(p, "programs", "X.exe", "wm_away"))
                self.assertFalse(recipes.pinned(p, "programs", "X.exe", "browser_software_gl"))
                self.assertFalse(recipes.pinned(p, "programs", "Other.exe", "rendering"))


class ApplyTest(unittest.TestCase):
    def test_program_rendering_writes_the_per_exe_overrides(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = FakePrefix(tmp); key = recipes.overrides_key("X.exe")
            self.assertEqual(recipes.apply_program(p, "X.exe", {"rendering": "wine"}), ["rendering=wine"])
            self.assertEqual(p.values[key], {"d3d10core": "builtin", "d3d11": "builtin", "dxgi": "builtin"})
            self.assertEqual(recipes.apply_program(p, "X.exe", {"rendering": "wine"}), [])        # idempotent
            recipes.apply_program(p, "X.exe", {"rendering": "gdi"})
            self.assertEqual(p.values[key]["d3d11"], "disabled"); self.assertEqual(p.values[key]["dxgi"], "disabled")
            self.assertNotIn("d3d9", p.values[key])                    # never: a program may import it directly
            recipes.apply_program(p, "X.exe", {"rendering": "dxvk"})
            self.assertEqual(p.values[key]["d3d11"], "native,builtin")
            self.assertEqual(recipes.apply_program(p, "X.exe", {}), ["rendering=auto"])
            self.assertEqual(p.values[key], {})

    def test_auto_hands_back_to_detection_when_the_exe_is_at_hand(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = FakePrefix(tmp); key = recipes.overrides_key("Fmx.exe")
            exe = Path(tmp) / "Fmx.exe"; exe.write_bytes(b"MZ" + b"\0" * 64 + b"FMX.Platform.Win\0FMX.Canvas.GDIP\0")
            recipes.apply_program(p, "Fmx.exe", {"rendering": "wine"})
            recipes.apply_program(p, "Fmx.exe", {}, exe_path=exe)
            self.assertEqual(p.values[key].get("d3d11"), "disabled")     # fmx's detection put its value back

    def test_window_manager_and_browser_knobs(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = FakePrefix(tmp)
            from vstenv import unmanaged, webview2
            done = recipes.apply_program(p, "K.exe", {"wm_away": "on", "browser_software_gl": "on"})
            self.assertEqual(sorted(done), ["browser_software_gl=on", "wm_away=on"])
            self.assertEqual(p.values[unmanaged.key("K.exe")]["Managed"], "N")
            self.assertIn(webview2.SOFTWARE_GL_FLAGS, p.values[webview2.POLICY_KEY]["K.exe"])
            recipes.apply_program(p, "K.exe", {"wm_away": "off", "browser_software_gl": "off"})
            self.assertNotIn("Managed", p.values[unmanaged.key("K.exe")])
            self.assertNotIn("swiftshader", p.values[webview2.POLICY_KEY]["K.exe"])

    def test_detection_leaves_a_pinned_program_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = FakePrefix(tmp)
            from vstenv import dcomp, fmx
            recipes.set_choice(p, "programs", "J.exe", rendering="dxvk")
            self.assertEqual(dcomp.apply_program_overrides(p, ["J.exe"]), [])
            self.assertFalse(fmx.apply(p, "J.exe"))
            self.assertEqual(p.values, {})

    def test_plugin_rendering_becomes_a_launcher_override_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = FakePrefix(tmp)
            f = _table(tmp, {"plugins": {"Tabled.vst3": {"rendering": "wine"}}})
            with mock.patch.object(recipes, "FETCHED", f):
                recipes.set_choice(p, "plugins", "Squash.vst3", rendering="gdi")
                self.assertEqual(recipes.plugin_overrides(p), [("Squash.vst3", "d3d11,dxgi=d"), ("Tabled.vst3", "d3d10core,d3d11,dxgi=b")])
                from vstenv import dcomp
                with mock.patch.object(dcomp, "plugin_overrides", return_value=[("Squash.vst3", "x=b"), ("Scanned.vst3", "d3d11=b")]), \
                     mock.patch("vstenv.vendors.all", return_value=[]):
                    allo = dcomp.all_plugin_overrides(p)
                self.assertEqual(allo[0], ("Squash.vst3", "d3d11,dxgi=d"))     # the choice beats the scan's entry
                self.assertIn(("Scanned.vst3", "d3d11=b"), allo)
                self.assertEqual([e for e in allo if e[0] == "Squash.vst3"], [("Squash.vst3", "d3d11,dxgi=d")])

    def test_hide_wine_is_per_plugin_and_can_be_turned_off(self):
        from vstenv import dcomp, yabridge
        class Vendor:
            def plugin_host_env(self, p): return [("/x/Squash.vst3", yabridge.HIDE_WINE_VERSION), ("/x/Formant.vst3", yabridge.HIDE_WINE_VERSION)]
        with tempfile.TemporaryDirectory() as tmp:
            p = FakePrefix(tmp)
            f = _table(tmp, {"plugins": {"Tabled.vst3": {"hide_wine": "on"}}})
            with mock.patch.object(recipes, "FETCHED", f), mock.patch("vstenv.vendors.all", return_value=[Vendor()]):
                recipes.set_choice(p, "plugins", "Formant.vst3", hide_wine="off")
                recipes.set_choice(p, "plugins", "Mine.vst3", hide_wine="on")
                self.assertEqual(recipes.plugin_hide_wine(p), (["Mine.vst3", "Tabled.vst3"], ["Formant.vst3"]))
                self.assertEqual([e[0] for e in dcomp.all_plugin_host_env(p)], ["Mine.vst3", "Tabled.vst3", "/x/Squash.vst3"])

    def test_in_effect_lists_who_set_what(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = FakePrefix(tmp)
            f = _table(tmp, {"programs": {"T.exe": {"rendering": "gdi"}}})
            with mock.patch.object(recipes, "FETCHED", f):
                recipes.set_choice(p, "plugins", "S.vst3", rendering="wine")
                lines = recipes.in_effect(p)
            self.assertIn("T.exe: rendering=gdi (recipe)", lines)
            self.assertIn("S.vst3: rendering=wine (yours)", lines)


class ShareTest(unittest.TestCase):
    def test_share_text_carries_the_recipe_as_the_table_takes_it(self):
        with mock.patch.object(recipes, "environment", return_value={"vstenv": "0.1.0", "wine": "w", "dxvk": "3.1", "gpu": "GPU", "session": "x11 / X-Cinnamon", "distribution": "d"}):
            title, body = recipes.share_text(None, "programs", "X.exe", {"rendering": "wine", "wm_away": "auto"}, "blank window", "1.2")
        self.assertEqual(title, "Recipe: X.exe (1.2)")
        block = body[body.index("```json") + 7:body.index("```", body.index("```json") + 7)]
        entry = json.loads(block)["programs"]["X.exe"]
        self.assertEqual(entry["rendering"], "wine"); self.assertNotIn("wm_away", entry)
        self.assertEqual(entry["why"], "blank window"); self.assertEqual(entry["confirmed"], ["GPU; x11 / X-Cinnamon; vstenv 0.1.0"])
        self.assertEqual(recipes._clean(json.loads(block))["programs"]["X.exe"]["rendering"], "wine")   # the table accepts it as is
        self.assertIn("- gpu: GPU", body)

    def test_a_share_with_nothing_chosen_is_a_report(self):
        with mock.patch.object(recipes, "environment", return_value={"vstenv": "0.1.0", "wine": "w", "dxvk": "-", "gpu": "G", "session": "s", "distribution": "d"}):
            title, body = recipes.share_text(None, "programs", "X.exe", {"rendering": "auto"}, "it crashes")
        self.assertTrue(title.startswith("Report: X.exe")); self.assertIn("nothing changed", body)

    def test_share_url_is_a_prefilled_issue_link(self):
        with mock.patch.object(recipes, "environment", return_value={"vstenv": "0.1.0", "wine": "w", "dxvk": "-", "gpu": "G", "session": "s", "distribution": "d"}):
            url = recipes.share_url(None, "plugins", "S.vst3", {"rendering": "gdi"}, "controls vanish", worked=False)
        self.assertTrue(url.startswith(recipes.ISSUES_URL + "?"))
        self.assertIn("labels=recipe", url); self.assertIn("did+not+help", url); self.assertIn("controls+vanish", url)


if __name__ == "__main__":
    unittest.main()
