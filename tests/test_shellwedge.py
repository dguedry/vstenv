import os
import unittest
from unittest import mock

from vstenv import shellwedge


class DesktopTest(unittest.TestCase):
    def test_mutter_desktops_are_recognised(self):
        for d in ("GNOME", "gnome-xorg", "ubuntu:GNOME", "Pantheon", "Budgie:GNOME"):
            with mock.patch.dict(os.environ, {"XDG_CURRENT_DESKTOP": d}):
                self.assertTrue(shellwedge.on_mutter(), d)

    def test_other_desktops_are_not(self):
        for d in ("X-Cinnamon", "KDE", "XFCE", ""):
            with mock.patch.dict(os.environ, {"XDG_CURRENT_DESKTOP": d}):
                self.assertFalse(shellwedge.on_mutter(), d)


class DetectTest(unittest.TestCase):
    """Reported on Fedora 44 / GNOME: an installer ran, had a dock entry and
    answered the keyboard, but never showed a window. Mutter had a window stuck
    in unmanaging and was answering every map request with WM_STATE=Iconic."""

    def _detect(self, desktop, count):
        with mock.patch.dict(os.environ, {"XDG_CURRENT_DESKTOP": desktop}), \
             mock.patch.object(shellwedge, "ping_assertions", return_value=count):
            return shellwedge.detect()

    def test_many_assertions_on_gnome_is_the_wedge(self):
        d = self._detect("GNOME", 350)
        self.assertTrue(d["wedged"])
        self.assertIn("log out", d["why"].lower())

    def test_a_quiet_gnome_session_is_not_accused(self):
        self.assertFalse(self._detect("GNOME", 0)["wedged"])

    def test_a_few_assertions_are_not_enough(self):
        """One app that really hung logs this too; the wedge logs it constantly."""
        self.assertFalse(self._detect("GNOME", 5)["wedged"])

    def test_another_desktop_is_never_accused(self):
        """Cinnamon and KDE do not have this mutter bug, whatever the journal says."""
        d = self._detect("X-Cinnamon", 999)
        self.assertFalse(d["wedged"])
        self.assertIn("not a mutter desktop", d["why"])

    def test_no_journal_is_not_a_wedge(self):
        with mock.patch.dict(os.environ, {"XDG_CURRENT_DESKTOP": "GNOME"}), \
             mock.patch.object(shellwedge.host, "sh", return_value=""):
            self.assertFalse(shellwedge.detect()["wedged"])

    def test_a_garbled_journal_reply_is_not_a_wedge(self):
        with mock.patch.dict(os.environ, {"XDG_CURRENT_DESKTOP": "GNOME"}), \
             mock.patch.object(shellwedge.host, "sh", return_value="command not found\n"):
            self.assertEqual(shellwedge.ping_assertions(), 0)


class NoteTest(unittest.TestCase):
    def test_gnome_gets_advice_even_when_healthy(self):
        with mock.patch.dict(os.environ, {"XDG_CURRENT_DESKTOP": "GNOME"}), \
             mock.patch.object(shellwedge, "ping_assertions", return_value=0):
            self.assertIn("meta_window_can_ping", shellwedge.note())

    def test_other_desktops_get_no_note(self):
        with mock.patch.dict(os.environ, {"XDG_CURRENT_DESKTOP": "KDE"}):
            self.assertEqual(shellwedge.note(), "")


if __name__ == "__main__":
    unittest.main()


class DesktopFallbackTest(unittest.TestCase):
    """Flatpak passes XDG_CURRENT_DESKTOP through only when the launching
    environment had it. Measured on a Fedora GNOME host: the sandbox saw None
    while the session had GNOME, which would have disabled the check silently
    on the very platform it is for."""

    def test_the_variable_is_used_when_present(self):
        with mock.patch.dict(os.environ, {"XDG_CURRENT_DESKTOP": "GNOME"}), \
             mock.patch.object(shellwedge.host, "sh") as sh:
            self.assertEqual(shellwedge.desktop(), "gnome")
        sh.assert_not_called()

    def test_the_host_session_answers_when_the_variable_is_missing(self):
        env = dict(os.environ); env.pop("XDG_CURRENT_DESKTOP", None)
        with mock.patch.dict(os.environ, env, clear=True), \
             mock.patch.object(shellwedge.host, "sh", return_value="GNOME\n"):
            self.assertEqual(shellwedge.desktop(), "gnome")
            self.assertTrue(shellwedge.on_mutter())

    def test_no_variable_and_no_host_answer_is_empty_not_an_error(self):
        env = dict(os.environ); env.pop("XDG_CURRENT_DESKTOP", None)
        with mock.patch.dict(os.environ, env, clear=True), \
             mock.patch.object(shellwedge.host, "sh", return_value=""):
            self.assertEqual(shellwedge.desktop(), "")
            self.assertFalse(shellwedge.on_mutter())
