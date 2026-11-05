"""Minimal Audio: the Hub's download is the program itself, installed as one,
and its WebView2 window is switched to software rendering by exe name."""
import tempfile, unittest
from pathlib import Path
from unittest import mock

from vstenv import vendors, webview2
from vstenv.vendors.minimal import HUB_DIR, HUB_EXE, PRODUCTS_DIR, is_hub_app
from vstenv.wine import Prefix, WineBuild


def _prefix(tmp):
    p = Prefix(Path(tmp) / "prefix", WineBuild(Path(tmp) / "wine"))
    (p.drive_c / "users/me").mkdir(parents=True, exist_ok=True)
    return p


class RecognitionTest(unittest.TestCase):
    def setUp(self): self.v = vendors.get("minimal")

    def test_registered_as_a_builtin(self):
        self.assertIn("minimal", [v.id for v in vendors.all()])

    def test_recognises_the_hub_download(self):
        for n in ("Minimal Hub.exe", "Minimal-Hub-1.1.11.exe", "minimal_hub.exe"):
            self.assertEqual(vendors.classify_installer(Path(n))[0], "manager", n)
            self.assertEqual([x.id for x in vendors.all() if x.accepts_manager_installer(Path(n))], ["minimal"])

    def test_does_not_claim_its_plugins_or_other_vendors(self):
        for n in ("Current Installer.exe", "Rift 2 Setup.exe", "Native-Access-2.exe", "minimalistic-icons.exe"):
            self.assertFalse(self.v.accepts_manager_installer(Path(n)), n)

    def test_the_hub_is_run_through_the_app(self):
        from vstenv.programs import Program
        self.assertTrue(vendors.run_through_app(Program(name="Minimal Hub", exe=r"C:\x\Minimal Hub.exe")))

    def test_hub_app_is_told_from_a_setup_by_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = Path(tmp) / "Minimal Hub.exe"; app.write_bytes(b"MZ" + b"\0" * 32 + b"__TAURI_INTERNALS__")
            setup = Path(tmp) / "Minimal Hub Setup.exe"; setup.write_bytes(b"MZ" + b"\0" * 32 + b"Nullsoft")
            self.assertTrue(is_hub_app(app)); self.assertFalse(is_hub_app(setup))
            self.assertFalse(is_hub_app(Path(tmp) / "missing.exe"))


class InstallTest(unittest.TestCase):
    def setUp(self): self.v = vendors.get("minimal")

    def test_the_app_itself_is_copied_into_program_files_and_given_its_flags(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = _prefix(tmp)
            src = Path(tmp) / "Minimal Hub.exe"; src.write_bytes(b"MZ" + b"\0" * 32 + b"__TAURI_INTERNALS__")
            calls = []
            with mock.patch.object(Prefix, "run", lambda self, args, **kw: calls.append(args) or mock.Mock(returncode=0)):
                self.v.install_manager(p, src)
            self.assertTrue((p.drive_c / HUB_DIR / HUB_EXE).is_file())
            self.assertTrue(self.v.manager_installed(p))
            reg = [a for a in calls if a[:2] == ["reg", "add"]]
            self.assertEqual(len(reg), 1)
            self.assertEqual(reg[0][4], HUB_EXE)
            self.assertIn(webview2.SOFTWARE_GL_FLAGS, reg[0][8])
            self.assertIn(webview2.PRESENTATION_FLAGS, reg[0][8])

    def test_a_real_setup_would_be_run_not_copied(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = _prefix(tmp)
            src = Path(tmp) / "Minimal Hub Setup.exe"; src.write_bytes(b"MZ" + b"\0" * 32 + b"Nullsoft")
            with mock.patch("vstenv.programs.install") as inst:
                self.v.install_manager(p, src)
            inst.assert_called_once()
            self.assertFalse((p.drive_c / HUB_DIR / HUB_EXE).exists())

    def test_prepare_does_nothing_without_the_hub(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = _prefix(tmp)
            with mock.patch.object(Prefix, "run") as run:
                self.v.prepare(p)
            run.assert_not_called()

    def test_software_gl_is_forced_even_when_the_prefix_setting_is_off(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = _prefix(tmp)
            with mock.patch.object(webview2, "software_gl", return_value=False):
                f = self.v.flags(p)
            self.assertIn("--use-gl=swiftshader", f)
            self.assertEqual(f.count("--use-gl=swiftshader"), 1)
            with mock.patch.object(webview2, "software_gl", return_value=True):
                self.assertEqual(self.v.flags(p).count("--use-gl=swiftshader"), 1)


class InventoryTest(unittest.TestCase):
    def setUp(self): self.v = vendors.get("minimal")

    def test_hub_is_listed_as_a_program_only_when_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = _prefix(tmp)
            self.assertEqual(self.v.programs(p), [])
            exe = p.drive_c / HUB_DIR / HUB_EXE; exe.parent.mkdir(parents=True); exe.write_bytes(b"MZ")
            progs = self.v.programs(p)
            self.assertEqual([x.name for x in progs], ["Minimal Hub"])
            self.assertTrue(progs[0].exe.endswith("\\" + HUB_EXE))
            self.assertEqual(progs[0].sources, ["vendor"])
            self.assertTrue(self.v.is_manager_program(progs[0]))
            self.assertFalse(progs[0].background)

    def test_launching_starts_the_exe_once_and_does_not_recurse(self):
        """programs.run sends a vendor-sourced manager to launch_manager; the
        module's launch must take the generic path, or the two call each other."""
        with tempfile.TemporaryDirectory() as tmp:
            p = _prefix(tmp)
            exe = p.drive_c / HUB_DIR / HUB_EXE; exe.parent.mkdir(parents=True); exe.write_bytes(b"MZ")
            spawned = []
            with mock.patch.object(Prefix, "spawn", lambda self, argv, **kw: spawned.append(argv) or mock.Mock(pid=1)), \
                 mock.patch("vstenv.launchwait.watch_and_resync", return_value=None):
                from vstenv import programs
                programs.run(p, self.v.programs(p)[0])      # the Programs page's Run
                self.v.launch_manager(p)                     # the manager row's Open
            self.assertEqual(len(spawned), 2)
            self.assertTrue(all(a[0].endswith("\\" + HUB_EXE) for a in spawned), spawned)

    def test_products_come_from_the_minimal_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = _prefix(tmp)
            for n in ("Current", "Rift"): (p.drive_c / PRODUCTS_DIR / n).mkdir(parents=True)
            with mock.patch("vstenv.programs.installed", return_value=[]):
                self.assertEqual([x.name for x in self.v.products(p)], ["Current", "Rift"])

    def test_health_flags_a_missing_software_gl_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = _prefix(tmp)
            exe = p.drive_c / HUB_DIR / HUB_EXE; exe.parent.mkdir(parents=True); exe.write_bytes(b"MZ")
            with mock.patch.object(Prefix, "reg_query", return_value={HUB_EXE: webview2.PRESENTATION_FLAGS}):
                c = {x.name: x.ok for x in self.v.checks(p)}
            self.assertTrue(c["Minimal Hub installed"]); self.assertFalse(c["Minimal Hub software rendering"])
            with mock.patch.object(Prefix, "reg_query", return_value={HUB_EXE: self.v.flags(p)}):
                self.assertTrue({x.name: x.ok for x in self.v.checks(p)}["Minimal Hub software rendering"])


if __name__ == "__main__":
    unittest.main()
