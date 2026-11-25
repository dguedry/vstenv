import json, tempfile, unittest
from pathlib import Path
from unittest import mock
from vstenv import menu_colors, paths, recipes

KDE = "[Colors:View]\nBackgroundNormal=1,2,3\n\n[Colors:Window]\nBackgroundNormal = 49,54,59\nForegroundNormal=252,252,252\n"

class FakePrefix:
    def __init__(self, d, rc=0): self.drive_c = Path(d); self.calls = []; self.rc = rc
    def run(self, args, **kw):
        self.calls.append(args); return mock.Mock(returncode=self.rc)

class MenuColorsTest(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp()); self.kde = self.d / "kdeglobals"
        p = mock.patch.object(menu_colors.host, "desktop_tool", return_value=None); p.start(); self.addCleanup(p.stop)

    def test_parse(self):
        self.kde.write_text(KDE)
        self.assertEqual(menu_colors.plasma_colors(self.kde),
                         {"Menu": "49 54 59", "MenuBar": "49 54 59", "MenuText": "252 252 252"})

    def test_missing_or_malformed(self):
        self.assertIsNone(menu_colors.plasma_colors(self.d / "none"))
        for body in ("", "[Colors:Window]\nBackgroundNormal=1,2,3\n", "[Colors:View]\nBackgroundNormal=1,2,3\nForegroundNormal=4,5,6\n",
                     "[Colors:Window]\nBackgroundNormal=1,2\nForegroundNormal=4,5,6\n",
                     "[Colors:Window]\nBackgroundNormal=a,b,c\nForegroundNormal=4,5,6\n",
                     "[Colors:Window]\nBackgroundNormal=1,2,300\nForegroundNormal=4,5,6\n"):
            self.kde.write_text(body); self.assertIsNone(menu_colors.plasma_colors(self.kde), body)

    def test_xdg_config_home(self):
        (self.d / "kdeglobals").write_text(KDE)
        with mock.patch.dict("os.environ", {"XDG_CONFIG_HOME": str(self.d)}), mock.patch.object(paths, "_IN_FLATPAK", False):
            self.assertIsNotNone(menu_colors.plasma_colors())

    def test_flatpak_reads_the_hosts_config_not_the_remapped_one(self):
        (self.d / "kdeglobals").write_text(KDE); (self.d / ".config").mkdir(); (self.d / ".config/kdeglobals").write_text(KDE)
        remapped = self.d / "remapped"; remapped.mkdir()
        with mock.patch.dict("os.environ", {"XDG_CONFIG_HOME": str(remapped)}), mock.patch.object(paths, "_IN_FLATPAK", True), \
             mock.patch.object(Path, "home", return_value=self.d):
            self.assertIsNotNone(menu_colors.plasma_colors())

    def test_reg_command_then_noop_when_unchanged(self):
        self.kde.write_text(KDE); p = FakePrefix(self.d)
        self.assertTrue(menu_colors.apply(p, self.kde))
        self.assertIn(["reg", "add", "HKCU\\Control Panel\\Colors", "/v", "MenuText", "/t", "REG_SZ", "/d", "252 252 252", "/f"], p.calls)
        self.assertEqual(len(p.calls), 3)
        self.assertFalse(menu_colors.apply(p, self.kde)); self.assertEqual(len(p.calls), 3)
        self.kde.write_text(KDE.replace("49,54,59", "10,10,10"))
        self.assertTrue(menu_colors.apply(p, self.kde)); self.assertEqual(len(p.calls), 6)

    def test_nothing_written_without_kdeglobals_or_on_failure(self):
        p = FakePrefix(self.d); self.assertFalse(menu_colors.apply(p, self.d / "none")); self.assertEqual(p.calls, [])
        self.kde.write_text(KDE); p = FakePrefix(self.d, rc=1)
        self.assertFalse(menu_colors.apply(p, self.kde)); self.assertFalse((self.d / menu_colors.STATE).exists())

    def test_off_by_default(self):
        self.assertIn("desktop_menu_colors", recipes.KNOBS["programs"])
        p = FakePrefix(self.d)
        with mock.patch.object(menu_colors, "apply") as ap, mock.patch.object(recipes, "overrides_key", return_value="K"):
            p.reg_query = lambda k: {}
            recipes.apply_program(p, "x.exe", {})
            ap.assert_not_called()
            recipes.apply_program(p, "x.exe", {"desktop_menu_colors": "on"})
            ap.assert_called_once()

    def test_turning_it_off_deletes_the_values_once(self):
        self.kde.write_text(KDE); p = FakePrefix(self.d); menu_colors.apply(p, self.kde); p.calls.clear()
        with mock.patch.object(recipes, "overrides_key", return_value="K"):
            p.reg_query = lambda k: {}
            self.assertEqual(recipes.apply_program(p, "x.exe", {}), ["desktop_menu_colors=off"])
            self.assertEqual(p.calls, [["reg", "delete", menu_colors.KEY, "/v", k, "/f"] for k in ("Menu", "MenuBar", "MenuText")])
            self.assertFalse((self.d / menu_colors.STATE).exists())
            p.calls.clear(); recipes.apply_program(p, "x.exe", {}); self.assertEqual(p.calls, [])

class GnomeTest(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp()); self.kde = self.d / "kdeglobals"

    def gs(self, **vals):
        def tool(cmd, timeout=30):
            self.assertEqual(timeout, 3)
            v = vals.get(cmd[-1])
            return None if v is None else mock.Mock(returncode=0 if v != "ERR" else 1, stdout=v)
        return mock.patch.object(menu_colors.host, "desktop_tool", side_effect=tool)

    def test_schemes(self):
        dark = {"Menu": "36 36 36", "MenuBar": "36 36 36", "MenuText": "255 255 255"}
        light = {"Menu": "250 250 250", "MenuBar": "250 250 250", "MenuText": "51 51 51"}
        for out, want in (("'prefer-dark'\n", dark), ("'default'\n", light), ("'prefer-light'\n", light), ("'weird'\n", None)):
            with self.gs(**{"color-scheme": out}): self.assertEqual(menu_colors.gnome_colors(), want, out)

    def test_gtk_theme_fallback_only_when_scheme_fails(self):
        with self.gs(**{"color-scheme": "ERR", "gtk-theme": "'Adwaita-Dark'\n"}): self.assertEqual(menu_colors.gnome_colors()["Menu"], "36 36 36")
        with self.gs(**{"gtk-theme": "'Yaru-dark'\n"}): self.assertEqual(menu_colors.gnome_colors()["MenuText"], "255 255 255")
        with self.gs(**{"color-scheme": "ERR", "gtk-theme": "'Adwaita'\n"}): self.assertIsNone(menu_colors.gnome_colors())
        with self.gs(): self.assertIsNone(menu_colors.gnome_colors())      # no gsettings

    def test_timeout_and_missing_tool(self):
        with mock.patch.object(menu_colors.host, "run", side_effect=__import__("subprocess").TimeoutExpired("gsettings", 3)), \
             mock.patch.object(menu_colors.host, "which", return_value="/usr/bin/gsettings"):
            self.assertIsNone(menu_colors.gnome_colors())

    def test_desktop_selection(self):
        self.kde.write_text(KDE)
        kde = menu_colors.plasma_colors(self.kde)
        with self.gs(**{"color-scheme": "'prefer-light'\n"}):
            for desk, want in (("GNOME", "250 250 250"), ("ubuntu:GNOME", "250 250 250"), ("X-Cinnamon", "250 250 250"),
                               ("KDE", kde["Menu"]), ("", kde["Menu"])):
                with mock.patch.dict("os.environ", {"XDG_CURRENT_DESKTOP": desk}):
                    self.assertEqual(menu_colors.desktop_colors(self.kde)["Menu"], want, desk)
        with self.gs(), mock.patch.dict("os.environ", {"XDG_CURRENT_DESKTOP": "GNOME"}):
            self.assertEqual(menu_colors.desktop_colors(self.kde), kde)          # falls back to kdeglobals
        with self.gs(**{"color-scheme": "'prefer-dark'\n"}), mock.patch.dict("os.environ", {"XDG_CURRENT_DESKTOP": "KDE"}):
            self.assertEqual(menu_colors.desktop_colors(self.d / "none")["Menu"], "36 36 36")   # no kdeglobals

if __name__ == "__main__":
    unittest.main()
