"""The Programs page's actions: what they confirm, keep and refresh.

These read gui.py rather than driving GTK: the window needs a display and a
prepared prefix, and the parts worth protecting here are decisions, not
rendering.
"""
import re
import unittest
from pathlib import Path


class GuiActionsTest(unittest.TestCase):
    def setUp(self):
        self.src = Path("vstenv/gui.py").read_text()

    def _body(self, name, chars=1800):
        """The source of one method, stopping at the next def so a window does
        not spill into the method after it."""
        i = self.src.index(f"def {name}(self")
        nxt = self.src.find("\n    def ", i + 1)
        end = min(i + chars, nxt if nxt != -1 else len(self.src))
        return self.src[i:end]

    # -- one window per app -----------------------------------------------------
    def test_activating_a_running_app_reuses_its_window(self):
        """The app is single-instance: a launcher click while it runs is a second
        activation. It used to open another window each time (three cascaded
        windows after two clicks); it must raise the one that exists."""
        body = self._body("do_activate")
        self.assertIn("get_windows()", body)
        self.assertEqual(body.count("Window(self)"), 1)
        self.assertIn("present()", body)

    # -- uninstall asks first ---------------------------------------------------
    def test_uninstall_confirms_before_running_anything(self):
        """The trash icon sits next to Run; a mis-click costs paid software."""
        body = self._body("uninstall_program")
        self.assertIn("AlertDialog", body)
        self.assertNotIn("run_bg(", body)          # the work happens after the answer

    def test_uninstall_defaults_to_cancel_and_marks_the_action_destructive(self):
        body = self._body("uninstall_program")
        self.assertIn('set_default_response("cancel")', body)
        self.assertIn("DESTRUCTIVE", body)

    def test_uninstalling_a_vendor_manager_says_what_that_costs(self):
        body = self._body("uninstall_program")
        self.assertIn("is_manager_program", body)

    def test_the_confirmed_path_runs_the_uninstall(self):
        self.assertIn("programs.uninstall(", self._body("_do_uninstall"))

    # -- a finished task stays readable ----------------------------------------
    def test_a_finished_task_page_is_not_hidden(self):
        """It used to vanish three seconds after success, taking the only
        record of what happened with it."""
        body = self._body("_hide_task_page")
        self.assertNotIn("set_visible(False)", body)

    # -- the busy refusal is not a dead end ------------------------------------
    def test_busy_names_the_running_task(self):
        body = self._body("run_bg")
        self.assertIn("_busy_title", body)
        self.assertIn("button_label", body)

    def test_busy_title_is_cleared_when_the_task_ends(self):
        self.assertIn("self._busy_title = None", self._body("run_bg", 2600))

    # -- lazy refresh -----------------------------------------------------------
    def test_refresh_all_only_refreshes_the_visible_page(self):
        """Reading the plugin list costs seconds in the prefix; refreshing all
        five pages after every action spent them on pages nobody was viewing."""
        body = self._body("refresh_all")
        self.assertIn("_stale", body)
        self.assertIn("get_visible_child_name", body)

    def test_a_stale_page_is_refreshed_when_shown(self):
        body = self._body("_refresh_stale_page")
        self.assertIn("_stale", body)
        self.assertIn("discard", body)

    def test_every_refreshable_page_has_a_refresh_method(self):
        m = re.search(r"_PAGE_REFRESH = \{(.*?)\}", self.src, re.S)
        self.assertIsNotNone(m)
        for fn in re.findall(r'"(refresh_\w+)"', m.group(1)):
            self.assertIn(f"def {fn}(self", self.src, fn)

    def test_the_stack_notifies_the_stale_refresh(self):
        self.assertIn('connect("notify::visible-child-name", self._refresh_stale_page)', self.src)

    # -- background components are grouped --------------------------------------
    def test_background_programs_go_behind_an_expander(self):
        body = self._body("_draw_programs", 4000)
        self.assertIn("x.background", body)
        self.assertIn("Background components", body)

    def test_the_window_manager_switch_says_it_is_usually_unnecessary(self):
        from vstenv import unmanaged
        self.assertTrue(unmanaged.EXPLAIN.startswith(unmanaged.UNNECESSARY))

    # -- troubleshoot: the recipe knobs, per program and per plugin ------------------
    def test_troubleshoot_dialog_covers_the_knobs_and_says_it_is_usually_unnecessary(self):
        i = self.src.index("class TroubleshootDialog"); body = self.src[i:self.src.index("\ndef _noted_row", i)]
        self.assertIn("recipes.UNNECESSARY", body)
        for knob in ("rendering", "wm_away", "browser_software_gl"): self.assertIn(f'"{knob}"', body)
        self.assertIn("recipes.set_choice(", body); self.assertIn("recipes.reset(", body)
        self.assertIn("recipes.share_url(", body); self.assertIn("Gtk.UriLauncher", body)
        self.assertIn("recipes.RENDERING_TEXT", body)          # every option explains itself

    def test_both_pages_offer_troubleshoot(self):
        self.assertIn("self.troubleshoot_program(prog, hosts)", self._body("_draw_programs", 4000))
        self.assertIn("self.troubleshoot_plugin(name)", self._body("_draw_plugins", 4000))

    def test_a_failed_install_ends_in_remedies_not_a_toast(self):
        body = self._body("install_program", 2500)
        self.assertIn("InstallFailedDialog(self, path, err)", body)
        self.assertIn("remedy=remedy", body); self.assertIn("self.remedy_worked(path, remedy)", body)
        i = self.src.index("class InstallFailedDialog"); dlg = self.src[i:self.src.index("\ndef _noted_row", i)]
        self.assertIn("remedies.options(", dlg); self.assertIn("self.win.install_program(self.path, remedy=settings)", dlg)
        self.assertIn('"installs"', dlg); self.assertIn("worked=False", dlg); self.assertIn("report.scrub(", dlg)
        kept = self._body("remedy_worked")
        self.assertIn('recipes.set_choice(self.prefix, "installs", name, **remedy)', kept); self.assertIn("worked=True", kept)

    def test_applying_a_recipe_takes_the_right_path_per_kind(self):
        body = self._body("apply_recipe")
        self.assertIn("recipes.apply_program(", body)                 # a program: a few registry values
        self.assertIn("setup.after_change(self.prefix, r, force=True)", body)   # a plugin: the launcher's file, rewritten
        self.assertIn("restart it", body); self.assertIn("reload it in your DAW", body)


if __name__ == "__main__":
    unittest.main()
