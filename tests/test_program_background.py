"""Which installed programs are plumbing rather than something to open.

The Programs page puts background components behind an expander, so getting
this wrong either hides a program the user wants or clutters the page.
"""
import unittest

from vstenv.programs import Program


def prog(name, sources, exe="C:\\x.exe", publisher=""):
    return Program(name=name, sources=list(sources), exe=exe, publisher=publisher)


class BackgroundTest(unittest.TestCase):
    def test_a_start_menu_shortcut_means_a_person_opens_it(self):
        self.assertFalse(prog("Kontakt 8", ["registry", "shortcut"]).background)

    def test_registry_only_is_background(self):
        self.assertTrue(prog("Bonjour", ["registry"]).background)
        self.assertTrue(prog("PACE License Support Win64", ["registry"]).background)
        self.assertTrue(prog("Microsoft Edge WebView2 Runtime", ["registry"]).background)

    def test_a_plugin_with_no_standalone_is_background(self):
        self.assertTrue(prog("ValhallaFreqEcho version 1.2.0", ["registry"], exe="").background)

    def test_registry_only_apps_people_do_open_are_not_hidden(self):
        """Some installers write no shortcut; these are still programs."""
        for name in ("Arturia Software Center 2.12.0", "Steinberg Install Assistant",
                     "Steinberg MediaBay", "iLok License Manager"):
            self.assertFalse(prog(name, ["registry"]).background, name)

    def test_matching_is_case_insensitive(self):
        self.assertFalse(prog("ARTURIA SOFTWARE CENTER", ["registry"]).background)

    def test_a_vendor_program_without_a_shortcut_is_not_background(self):
        """A vendor module naming its own program is evidence enough."""
        p = prog("Native Access", ["vendor"])
        self.assertFalse(p.background)

    def test_vendor_id_source_counts_as_vendor_knowledge(self):
        self.assertFalse(prog("Absynth 5", ["ni"]).background)

    def test_a_vendor_driver_known_only_to_the_registry_stays_background(self):
        self.assertTrue(prog("Native Instruments Komplete Kontrol Driver", ["registry"], exe="").background)


if __name__ == "__main__":
    unittest.main()
