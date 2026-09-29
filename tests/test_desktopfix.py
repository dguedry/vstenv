"""On Cinnamon, vstenv switches focus-new-windows to strict so Wine windows stop
raising themselves; on other desktops it does nothing, and it never overrides a
user's own non-default choice."""
import unittest
from unittest import mock
from vstenv import desktopfix, host


class DesktopFixTest(unittest.TestCase):
    def _host(self, schemas="", value="smart", set_ok=True):
        """Fake host.run for gsettings list-schemas / get / set."""
        state = {"value": value}
        def fake_run(cmd, **kw):
            import subprocess
            args = cmd[1:] if cmd and cmd[0] == "gsettings" else cmd
            out, rc = "", 0
            if args[:1] == ["list-schemas"]: out = schemas
            elif args[:1] == ["get"]: out = f"'{state['value']}'"
            elif args[:1] == ["set"]:
                if set_ok: state["value"] = args[-1]
                else: rc = 1
            return subprocess.CompletedProcess(cmd, rc, out, "")
        return fake_run, state

    def test_not_cinnamon_does_nothing(self):
        fake, _ = self._host(schemas="org.gnome.desktop.wm.preferences")
        with mock.patch.object(host, "run", side_effect=fake):
            self.assertFalse(desktopfix.is_cinnamon())
            self.assertTrue(desktopfix.apply())          # no-op success
            self.assertFalse(desktopfix.status()["applies"])

    def test_cinnamon_default_is_switched_to_strict(self):
        fake, state = self._host(schemas=desktopfix.SCHEMA, value="smart")
        with mock.patch.object(host, "run", side_effect=fake):
            self.assertTrue(desktopfix.is_cinnamon())
            self.assertTrue(desktopfix.apply())
            self.assertEqual(state["value"], "strict")

    def test_user_non_default_choice_is_left_alone(self):
        fake, state = self._host(schemas=desktopfix.SCHEMA, value="mouse")   # a value we did not set
        with mock.patch.object(host, "run", side_effect=fake):
            self.assertTrue(desktopfix.apply())          # returns True but does not change it
            self.assertEqual(state["value"], "mouse")

    def test_already_strict_is_a_noop(self):
        fake, state = self._host(schemas=desktopfix.SCHEMA, value="strict")
        with mock.patch.object(host, "run", side_effect=fake):
            self.assertTrue(desktopfix.status()["ok"])
            self.assertTrue(desktopfix.apply())
            self.assertEqual(state["value"], "strict")

    def test_set_failure_is_reported(self):
        fake, _ = self._host(schemas=desktopfix.SCHEMA, value="smart", set_ok=False)
        with mock.patch.object(host, "run", side_effect=fake):
            self.assertFalse(desktopfix.apply())


if __name__ == "__main__": unittest.main()
