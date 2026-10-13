import unittest
from pathlib import Path


class RescueActionTest(unittest.TestCase):
    """`vstenv rescue-install` had no GUI equivalent: the menu only offered
    "Finish interrupted installs", which completes an install whose payload is
    already unpacked, not one whose installer is still sitting there stuck."""

    def setUp(self):
        self.src = Path("vstenv/gui.py").read_text()

    def test_the_menu_offers_rescue(self):
        self.assertIn("Rescue a stuck install", self.src)

    def test_rescue_and_finish_are_both_offered(self):
        """They solve different problems; offering only one is what sent a user
        to 'Re-run setup / repair', which does neither."""
        self.assertIn("Rescue a stuck install", self.src)
        self.assertIn("Finish interrupted installs", self.src)

    def test_rescue_calls_rescue_installs_not_finish(self):
        start = self.src.index("def rescue_installs(self)")
        body = self.src[start:start + 1200]
        self.assertIn("v.rescue_installs(", body)
        self.assertNotIn("v.finish_installs(", body)

    def test_finish_still_calls_finish(self):
        start = self.src.index("def finish_installs(self)")
        body = self.src[start:start + 700]
        self.assertIn("v.finish_installs(", body)

    def test_rescue_skips_the_environment_pass_when_nothing_was_rescued(self):
        """after_change bridges and rebuilds menus; running it for nothing is
        the minute-long pass that makes a no-op look like work."""
        start = self.src.index("def rescue_installs(self)")
        body = self.src[start:start + 1200]
        skip = body.index("none found")
        after = body.index("after_change")
        self.assertLess(skip, after, "after_change must be in the else branch")

    def test_the_cli_command_still_exists(self):
        cli = Path("vstenv/cli.py").read_text()
        self.assertIn("rescue-install", cli)


if __name__ == "__main__":
    unittest.main()
