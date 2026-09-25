"""The vendor registry and the interface the core relies on."""
import tempfile, unittest
from unittest import mock
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
        self.assertEqual(vendors.for_manager_installer(Path("Steinberg_Download_Assistant_1.40.1_Installer_win.exe")).id, "steinberg")
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


class SteinbergTest(unittest.TestCase):
    def _prefix(self, existing):
        from unittest import mock
        p = mock.Mock(); p.reg_query.return_value = existing; return p
    def test_launcher_cfg_gets_grey_text_masks_in_the_always_applied_section(self):
        import tempfile
        from vstenv.vendors import steinberg as sb
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "Steinberg Download Assistant.cfg"
            cfg.write_text("[Application]\napp.name=SDA\n\n[JVMOptions]\n-Dapplication.year=2026\n-Djava.net.useSystemProxies=true\n\n[JVMUserOptions]\n\n[ArgOptions]\n")
            self.assertFalse(sb.cfg_fixed(cfg)); self.assertTrue(sb.fix_cfg(cfg)); self.assertTrue(sb.cfg_fixed(cfg))
            text = cfg.read_text()
            self.assertIn("[JVMOptions]\n" + sb.CFG_MARK + "\n-Dprism.lcdtext=false\n-Dapplication.year=2026\n", text)
            self.assertIn("[JVMUserOptions]\n\n[ArgOptions]\n", text)      # the rest untouched
            self.assertFalse(sb.fix_cfg(cfg), "idempotent")
            self.assertEqual(text, cfg.read_text())
    def test_text_fix_sets_builtin_d3d_for_the_app_only(self):
        from vstenv.vendors import steinberg as sb
        p = self._prefix({})
        with mock.patch.object(sb, "launcher_cfg", return_value=Path("/nonexistent/x.cfg")): self.assertTrue(sb.apply_text_fix(p))
        keys = {c.args[0] for c in p.reg_add.call_args_list}; dlls = {c.args[1] for c in p.reg_add.call_args_list}
        self.assertEqual(keys, {sb.OVERRIDES_KEY}); self.assertEqual(dlls, set(sb.OVERRIDES))
        self.assertIn("AppDefaults\\Steinberg Download Assistant.exe", sb.OVERRIDES_KEY)
    def test_text_fix_is_idempotent(self):
        from vstenv.vendors import steinberg as sb
        p = self._prefix({d: "builtin" for d in sb.OVERRIDES})
        with mock.patch.object(sb, "launcher_cfg", return_value=Path("/nonexistent/x.cfg")):
            self.assertFalse(sb.apply_text_fix(p)); p.reg_add.assert_not_called()
        with mock.patch.object(sb, "cfg_fixed", return_value=True), mock.patch.object(sb, "launcher_cfg", return_value=Path("/x.cfg")): self.assertTrue(sb.text_fix_applied(p))
    def test_declares_the_login_callback_scheme(self):
        v = vendors.get("steinberg")
        self.assertEqual([s.scheme for s in v.url_schemes(None)], ["net-steinberg-sda", "net-steinberg-activation-manager"])
