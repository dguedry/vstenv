"""Jamstix 4's editor: the host settings are declared per plugin, and none of them
reach another plugin's host."""
import unittest

from vstenv import vendors
from vstenv.vendors.rayzoon import DLL_OVERRIDES


class JamstixHostTest(unittest.TestCase):
    def setUp(self): self.v = vendors.get("rayzoon")

    def test_registered_as_a_builtin_without_a_manager(self):
        self.assertIn("rayzoon", [v.id for v in vendors.all()])
        self.assertIsNone(self.v.manager_name)

    def test_host_gets_dpi_awareness_and_no_direct3d(self):
        env = dict((val.split("=", 1)[0], val.split("=", 1)[1]) for _, val in self.v.plugin_host_env(None))
        self.assertEqual(env["WINE_DPI_AWARENESS"], "2")
        self.assertEqual(env["WINEDLLOVERRIDES"], DLL_OVERRIDES)
        for part in ("rtscom=d", "d3d11,dxgi=d"): self.assertIn(part, DLL_OVERRIDES)

    def test_matches_only_its_own_plugin(self):
        pat = self.v.plugin_host_env(None)[0][0]
        self.assertIn(pat, "/x/Program Files/Steinberg/VstPlugins/jamstix4.dll")
        self.assertNotIn(pat, "/x/Program Files/Steinberg/VstPlugins/Ozone 9.dll")
        self.assertNotIn(pat, "/x/Program Files/Steinberg/VstPlugins/audioM8.dll")
