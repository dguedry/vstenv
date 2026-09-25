"""The vendor registry and the interface the core relies on."""
import tempfile, unittest
from pathlib import Path
from vstenv import vendors
from vstenv.programs import Program

class VendorRegistryTest(unittest.TestCase):
    def test_builtins_load(self):
        ids = {v.id for v in vendors.all()}
        self.assertTrue(ids >= {"ni", "ik"}, ids)
        self.assertIs(vendors.get("ni"), vendors.get("Native Instruments"))
        with self.assertRaises(LookupError): vendors.get("nope")

    def test_every_vendor_honours_the_interface_defaults(self):
        for v in vendors.all():
            self.assertTrue(v.id and v.name)
            self.assertIsInstance(v.plugin_dirs(), list); self.assertIsInstance(v.quirks(), dict); self.assertIsInstance(v.launch_args(), dict)
            self.assertIsInstance(v.daemon_ports, tuple)

    def test_manager_installers_are_recognised_by_name(self):
        self.assertEqual(vendors.for_manager_installer(Path("~/Downloads/Native-Access-latest.exe")).id, "ni")
        self.assertEqual(vendors.for_manager_installer(Path("Native Access 3.26.0 Setup.exe")).id, "ni")
        self.assertEqual(vendors.for_manager_installer(Path("IK_Product_Manager_Installer.exe")).id, "ik")
        self.assertIsNone(vendors.for_manager_installer(Path("Kontakt 8 Setup PC.exe")))

    def test_product_installers(self):
        self.assertEqual(vendors.for_product_installer(Path("Kontakt 8 Setup PC.exe")).id, "ni")
        self.assertIsNone(vendors.for_product_installer(Path("SomeSynth-Setup.exe")))
        with tempfile.TemporaryDirectory() as d:
            import zipfile
            z = Path(d) / "Battery_4_Installer.zip"
            with zipfile.ZipFile(z, "w") as zf: zf.writestr("Battery 4 Setup PC.exe", b"MZ")
            self.assertEqual(vendors.for_product_installer(z).id, "ni")

    def test_programs_are_attributed_to_vendors(self):
        self.assertEqual(vendors.for_program(Program(name="Kontakt 8", publisher="Native Instruments")).id, "ni")
        self.assertEqual(vendors.for_program(Program(name="Native Instruments Dirt")).id, "ni")
        self.assertEqual(vendors.for_program(Program(name="AmpliTube 5", publisher="IK Multimedia")).id, "ik")
        self.assertEqual(vendors.for_program(Program(name="IK Product Manager")).id, "ik")
        self.assertIsNone(vendors.for_program(Program(name="Vital", publisher="Vital Audio")))

    def test_ik_quirks_and_ni_plugin_dirs(self):
        self.assertIn("IK Product Manager", vendors.get("ik").quirks())
        self.assertEqual(len(vendors.get("ik").quirks()["IK Product Manager"]), 2)
        self.assertIn("Program Files/Native Instruments/VSTPlugins 64 bit", vendors.get("ni").plugin_dirs())
        self.assertEqual(vendors.get("ni").daemon_ports, (7865, 5563, 5146))

if __name__ == "__main__": unittest.main()
