import unittest
from pathlib import Path


class DesktopFileTest(unittest.TestCase):
    """The desktop entry is what makes a double-clicked .exe reach this app."""

    def setUp(self):
        self.text = Path("data/io.github.dguedry.vstenv.desktop").read_text()
        self.fields = dict(
            line.split("=", 1) for line in self.text.splitlines()
            if "=" in line and not line.startswith("#"))

    def test_exec_takes_a_file(self):
        """Without %f the desktop passes nothing and the double-click is lost."""
        self.assertIn("%f", self.fields["Exec"])

    def test_offers_itself_for_windows_installers(self):
        mimes = self.fields["MimeType"].split(";")
        self.assertIn("application/x-ms-dos-executable", mimes)   # .exe
        self.assertIn("application/x-msi", mimes)                 # .msi

    def test_mimetype_list_is_terminated(self):
        """A trailing ';' is required by the spec; without it the last entry is
        silently dropped by some desktops."""
        self.assertTrue(self.fields["MimeType"].rstrip().endswith(";"))

    def test_still_launches_without_a_file(self):
        self.assertTrue(self.fields["Exec"].startswith("vstenv-gui"))


class OpenHandlingTest(unittest.TestCase):
    """do_open routes a double-clicked installer through the same funnel as the
    Install tab, so it gets the vendor's fixes rather than a bare Wine run."""

    def test_gui_declares_it_handles_open(self):
        src = Path("vstenv/gui.py").read_text()
        self.assertIn("HANDLES_OPEN", src,
                      "without the flag GTK ignores file arguments entirely")
        self.assertIn("def do_open", src)

    def test_opened_file_goes_through_install_any(self):
        src = Path("vstenv/gui.py").read_text()
        start = src.index("def install_opened")
        body = src[start:start + 1400]
        self.assertIn("install_any", body,
                      "an opened installer must take the same path as a picked one")

    def test_opened_file_is_checked_before_use(self):
        src = Path("vstenv/gui.py").read_text()
        start = src.index("def install_opened")
        body = src[start:start + 1400]
        self.assertIn(".exe", body)
        self.assertIn("exists()", body)


if __name__ == "__main__":
    unittest.main()
