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
        self.assertEqual(len(vendors.get("ik").quirks()["IK Product Manager"]), 3)  # os-info, disable-gpu, keep-links-in-window
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


class SteinbergCannotRunTest(unittest.TestCase):
    def test_programs_importing_dcomp_are_refused_with_a_reason(self):
        from unittest import mock
        v = vendors.get("steinberg"); p = mock.Mock(); prog = Program(name="Steinberg HALion Sonic 7", publisher="Steinberg Media Technologies GmbH", exe=r"C:\\x\\HALion Sonic.exe", install_dir=r"C:\\x")
        with mock.patch.object(v, "needs_dcomp", return_value=["graphics2d.dll"]), mock.patch.object(v, "dcomp_ready", return_value=False):
            self.assertIn("DirectComposition", v.cannot_run(p, prog))
        with mock.patch.object(v, "needs_dcomp", return_value=["graphics2d.dll"]), mock.patch.object(v, "dcomp_ready", return_value=True):
            self.assertIsNone(v.cannot_run(p, prog), "with the patched dcomp.dll in the Wine build they run")
        with mock.patch.object(v, "needs_dcomp", return_value=[]):
            self.assertIsNone(v.cannot_run(p, prog))
        self.assertIsNone(v.cannot_run(p, Program(name="Steinberg Download Assistant", exe="x.exe", install_dir=r"C:\\y")))
        with mock.patch.object(v, "needs_dcomp", return_value=["graphics2d.dll"]):
            self.assertIsNone(v.cannot_run(p, Program(name="Steinberg MediaBay", exe="s.exe", install_dir=r"C:\\m")), "runtime components run despite importing dcomp")
        with mock.patch.object(v, "needs_dcomp", return_value=["Qt6Gui.dll"]):
            self.assertIsNone(v.cannot_run(p, Program(name="Some Steinberg Tool", exe="t.exe", install_dir=r"C:\\t")), "only Steinberg's graphics2d is the tell")


class ProductNotesTest(unittest.TestCase):
    def test_every_builtin_vendor_notes_are_well_formed(self):
        from unittest import mock
        p = mock.Mock(); p.build = None
        for v in vendors.all():
            with mock.patch("vstenv.dxvk.status", return_value={"installed": True}):
                notes = v.product_notes(p)
            self.assertIsInstance(notes, list)
            for n in notes:
                self.assertIn(n.level, vendors.LEVELS); self.assertTrue(n.text.endswith("."), n.text)
    def test_named_note_beats_the_vendor_fallback(self):
        ni = vendors.get("ni").product_notes(None)
        self.assertEqual(vendors.note_for("Kontakt 8", ni).level, "patched")
        self.assertEqual(vendors.note_for("Massive X", ni).level, "works")
        self.assertIsNone(vendors.note_for("anything", []))
    def test_steinberg_gui_products_depend_on_the_patched_dcomp(self):
        from unittest import mock
        v = vendors.get("steinberg")
        with mock.patch.object(v, "dcomp_ready", return_value=False):
            self.assertEqual(vendors.note_for("Steinberg HALion Sonic 7", v.product_notes(None)).level, "cannot")
        with mock.patch.object(v, "dcomp_ready", return_value=True):
            self.assertEqual(vendors.note_for("Steinberg HALion Sonic 7", v.product_notes(None)).level, "patched")
            self.assertEqual(vendors.note_for("Steinberg Library Manager", v.product_notes(None)).level, "works")


class IKFinishInstallsTest(unittest.TestCase):
    """The IK Product Manager downloads a product then fails to launch its
    installer (Node exec double-quotes a parenthesised path); vstenv runs the
    downloaded installer itself and skips products already installed."""
    def _prefix(self, tmp):
        from vstenv.wine import Prefix, WineBuild
        p = Prefix(Path(tmp) / "prefix", WineBuild(Path(tmp) / "wine"))
        (p.drive_c / "users/me").mkdir(parents=True)
        return p

    def _downloads(self, p):
        d = p.user_dir / "Documents/IK Multimedia/IK Product Manager"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def test_staged_lists_downloaded_but_uninstalled_products(self):
        import tempfile
        from unittest import mock
        from vstenv import programs, vendors
        v = vendors.get("ik")
        with tempfile.TemporaryDirectory() as tmp:
            p = self._prefix(tmp); d = self._downloads(p)
            for name, files in {"SampleTank 4": ["Install SampleTank 4 (4.2.6).exe", "Install_SampleTank_4_(4.2.6).zip"],
                                "Hammond B-3X": ["Install Hammond B-3X (1.2.0).exe", "unins000.exe"]}.items():
                sub = d / name; sub.mkdir()
                for fn in files: (sub / fn).write_bytes(b"MZ")
            # SampleTank 4 is already installed; Hammond is not
            prog = programs.Program(name="SampleTank 4", exe=None, install_dir=r"C:\\Program Files\\IK Multimedia\\SampleTank 4")
            with mock.patch.object(programs, "installed", return_value=[prog]):
                staged = v.staged_installs(p)
            names = [n for n, _ in staged]
            self.assertIn("Hammond B-3X", names)
            self.assertNotIn("SampleTank 4", names)                 # already installed -> skipped
            # staged returns the download folder; the installer exe is resolved at run time
            folder = dict(staged)["Hammond B-3X"]
            self.assertTrue(folder.is_dir())

    def test_finish_runs_each_through_the_installer(self):
        import tempfile
        from unittest import mock
        from vstenv import programs, vendors
        v = vendors.get("ik")
        with tempfile.TemporaryDirectory() as tmp:
            p = self._prefix(tmp); d = self._downloads(p)
            sub = d / "T-RackS 5"; sub.mkdir(); (sub / "Install T-RackS 5 (5.9).exe").write_bytes(b"MZ")
            with mock.patch.object(programs, "installed", return_value=[]), \
                 mock.patch.object(programs, "install", return_value=0) as inst:
                done = v.finish_installs(p)
            inst.assert_called_once()
            self.assertEqual(done, [{"vendor": "ik", "product": "T-RackS 5", "ok": True}])

    def test_truncated_exe_is_reextracted_from_the_zip_before_running(self):
        import tempfile, zipfile
        from unittest import mock
        from vstenv import programs, vendors
        v = vendors.get("ik")
        with tempfile.TemporaryDirectory() as tmp:
            p = self._prefix(tmp); d = self._downloads(p)
            sub = d / "Hammond B-3X"; sub.mkdir()
            full = b"MZ" + b"\0" * 5000                                  # the real installer content
            zpath = sub / "Install_Hammond_B-3X_(1.3.5).zip"
            with zipfile.ZipFile(zpath, "w") as zf: zf.writestr("Install Hammond B-3X (1.3.5).exe", full)
            exe = sub / "Install Hammond B-3X (1.3.5).exe"
            exe.write_bytes(full[:1000])                                  # truncated, like the PM left it
            ran = {}
            def fake_install(prefix, path, r=None):
                ran["size"] = Path(path).stat().st_size; return 0
            with mock.patch.object(programs, "installed", return_value=[]),                  mock.patch.object(programs, "install", side_effect=fake_install):
                done = v.finish_installs(p)
            self.assertEqual(exe.stat().st_size, len(full))              # re-extracted to full size
            self.assertEqual(ran["size"], len(full))                     # and the full one was run
            self.assertTrue(done[0]["ok"])


class RespawningKillTest(unittest.TestCase):
    """kill_respawning kills a crash-looping app the supervisor keeps relaunching,
    parents-before-children, and stops when the set stays empty."""
    def test_kills_until_the_set_is_empty(self):
        import tempfile
        from unittest import mock
        from vstenv.wine import Prefix, WineBuild
        with tempfile.TemporaryDirectory() as tmp:
            p = Prefix(Path(tmp) / "prefix", WineBuild(Path(tmp) / "wine"))
            live = {1: "start.exe /exec", 2: "iZotope Product Portal.exe --type=renderer"}
            def processes(exe_name=None):
                return [(pid, cmd) for pid, cmd in live.items()]
            def kill_pids(pids, sig="TERM", wait=0):
                for x in pids: live.pop(int(x), None)          # this round's kill removes them; nothing respawns
            with mock.patch.object(p, "processes", side_effect=processes), \
                 mock.patch.object(p, "kill_pids", side_effect=kill_pids), \
                 mock.patch("time.sleep"):
                n = p.kill_respawning(lambda c: "Portal" in c or "start.exe" in c)
            self.assertEqual(n, 2); self.assertEqual(live, {})

    def test_nothing_matching_kills_nothing(self):
        import tempfile
        from unittest import mock
        from vstenv.wine import Prefix, WineBuild
        with tempfile.TemporaryDirectory() as tmp:
            p = Prefix(Path(tmp) / "prefix", WineBuild(Path(tmp) / "wine"))
            with mock.patch.object(p, "processes", return_value=[(9, "explorer.exe")]), \
                 mock.patch.object(p, "kill_pids") as kp, mock.patch("time.sleep"):
                self.assertEqual(p.kill_respawning(lambda c: "Portal" in c), 0)
                kp.assert_not_called()


class IKOpenExternalQuirkTest(unittest.TestCase):
    """The Product Manager loads its UI from ikmultimedia.com and opens a browser
    tab for its own in-app links; the quirk keeps IK links in the window."""
    def test_shim_injected_after_electron_require_once(self):
        from vstenv.vendors.ik import keep_ik_links_in_window, _OPENEXT_MARK
        src = b"const { app, shell } = require('electron')\nconst fs = require('fs')\n"
        out = keep_ik_links_in_window(src)
        self.assertIn(_OPENEXT_MARK, out)
        self.assertIn(b"openExternal", out)
        self.assertIn(b"ikmultimedia", out)
        # injected right after the electron require line, before the next line
        self.assertLess(out.index(_OPENEXT_MARK), out.index(b"const fs"))
        # idempotent: a second pass makes no change
        self.assertIsNone(keep_ik_links_in_window(out))
    def test_raises_when_no_electron_require(self):
        from vstenv.vendors.ik import keep_ik_links_in_window
        with self.assertRaises(LookupError):
            keep_ik_links_in_window(b"const fs = require('fs')\n")
    def test_registered_as_a_manager_quirk(self):
        v = vendors.get("ik")
        whats = [q.what for q in v.quirks()[v.manager_name]]
        self.assertTrue(any("keep IK's own in-app links" in w for w in whats))


class RunThroughAppTest(unittest.TestCase):
    """Vendor managers/installers must run through the app, not a desktop shortcut."""
    def _prog(self, name, publisher=""):
        from vstenv import programs
        return programs.Program(name=name, publisher=publisher, exe=rf"C:\\x\\{name}.exe", install_dir=r"C:\\x")
    def test_managers_and_wrapped_installers_run_through_app(self):
        for name in ("Native Access", "IK Product Manager", "Steinberg Download Assistant",
                     "Steinberg Install Assistant", "Product Portal", "Arturia Software Center 2.12.0"):
            self.assertTrue(vendors.run_through_app(self._prog(name)), name)
    def test_instruments_and_services_get_a_shortcut(self):
        for name in ("Kontakt 8", "Steinberg HALion Sonic 7", "Analog Lab V 5.12.5", "Spitfire Audio version 3.4.18",
                     "Ozone 9 Advanced", "iLok License Manager", "PACE License Support Win64", "Bonjour",
                     "Steinberg Activation Manager", "Steinberg Library Manager", "Steinberg MediaBay"):
            self.assertFalse(vendors.run_through_app(self._prog(name)), name)


class AudioModelingFinishInstallsTest(unittest.TestCase):
    """The Software Center's BitRock installers abort under Wine in GUI mode; the
    am module finds the staged download and finishes it unattended."""
    def _prefix(self, tmp):
        from vstenv.wine import Prefix, WineBuild
        p = Prefix(Path(tmp) / "prefix", WineBuild(Path(tmp) / "wine"))
        (p.drive_c / "users/me").mkdir(parents=True)
        return p

    def _stage(self, p, product="SWAMViolin", ver="3.12.3-2944"):
        d = p.user_dir / "AppData/Roaming/Audio Modeling/Software Center/temp/Svl3_123"
        d.mkdir(parents=True, exist_ok=True)
        exe = d / f"{product}-{ver}-windows-x64-installer.exe"
        exe.write_bytes(b"MZ")
        return exe

    def test_staged_found_and_installed_product_skipped(self):
        import tempfile
        v = vendors.get("am")
        with tempfile.TemporaryDirectory() as tmp:
            p = self._prefix(tmp); self._stage(p)
            self.assertEqual([n for n, _ in v.staged_installs(p)], ["SWAMViolin"])
            # once the product dir exists (spaces vs no spaces), it is skipped
            (p.drive_c / "Program Files/Audio Modeling/SWAM Violin").mkdir(parents=True)
            self.assertEqual(v.staged_installs(p), [])

    def test_finish_runs_unattended(self):
        import tempfile, subprocess
        from unittest import mock
        v = vendors.get("am")
        with tempfile.TemporaryDirectory() as tmp:
            p = self._prefix(tmp); self._stage(p)
            with mock.patch.object(p, "run", return_value=subprocess.CompletedProcess([], 0)) as run:
                done = v.finish_installs(p)
            args = run.call_args[0][0]
            self.assertIn("--mode", args); self.assertIn("unattended", args)
            self.assertTrue(args[0].startswith("C:\\") and args[0].endswith("-windows-x64-installer.exe"))
            self.assertEqual(done, [{"vendor": "am", "product": "SWAMViolin", "ok": True}])

    def test_after_install_finishes_silently_only_when_staged(self):
        import tempfile, subprocess
        from unittest import mock
        v = vendors.get("am")
        with tempfile.TemporaryDirectory() as tmp:
            p = self._prefix(tmp)
            with mock.patch.object(p, "run") as run:
                v.after_install(p)                    # nothing staged -> no run
            run.assert_not_called()
            self._stage(p)
            with mock.patch.object(p, "run", return_value=subprocess.CompletedProcess([], 0)) as run:
                v.after_install(p)
            run.assert_called_once()

    def test_prepare_sets_the_webview2_software_compositing_policy(self):
        # WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS is ignored under Wine (elevated);
        # the policy registry key is the channel that works -- Center only.
        import tempfile, subprocess
        from unittest import mock
        v = vendors.get("am")
        with tempfile.TemporaryDirectory() as tmp:
            p = self._prefix(tmp)
            with mock.patch.object(p, "run") as run:
                v.prepare(p)                          # no Center installed -> no key
            run.assert_not_called()
            (p.drive_c / "Program Files/Audio Modeling/Software Center").mkdir(parents=True)
            with mock.patch.object(p, "run", return_value=subprocess.CompletedProcess([], 0)) as run:
                v.prepare(p)
            args = run.call_args[0][0]
            self.assertEqual(args[0], "reg")
            self.assertIn(r"Edge\WebView2\AdditionalBrowserArguments", args[2])
            self.assertIn("Audio Modeling Software Center.exe", args)
            self.assertIn("--disable-direct-composition --ui-disable-partial-swap", args)

    def test_watcher_rescues_the_download_the_center_deletes(self):
        # The Center spawns `installer.exe "--mode unattended"` as one argument,
        # the install fails, and it deletes the download: the watcher's hard link
        # must survive that cleanup and feed staged_installs.
        import shutil, tempfile
        v = vendors.get("am")
        with tempfile.TemporaryDirectory() as tmp:
            p = self._prefix(tmp); exe = self._stage(p)
            self.assertTrue(v._center_attempt_pending(p))
            v._rescue_snapshot(p)
            shutil.rmtree(exe.parent.parent)          # the Center's cleanup
            self.assertFalse(v._center_attempt_pending(p))
            staged = v.staged_installs(p)
            self.assertEqual([n for n, _ in staged], ["SWAMViolin"])
            self.assertEqual(staged[0][1].read_bytes(), b"MZ")
            # once the product is in, the rescue copy goes
            (p.drive_c / "Program Files/Audio Modeling/SWAM Violin").mkdir(parents=True)
            v._rescue_clean(p)
            self.assertEqual(v.staged_installs(p), [])
            self.assertEqual(list(v._rescue_dir(p).glob("*.exe")), [])

    def test_watch_program_finishes_leftovers_once_the_center_exits(self):
        import subprocess, tempfile
        from unittest import mock
        from vstenv import programs
        v = vendors.get("am")
        with tempfile.TemporaryDirectory() as tmp:
            p = self._prefix(tmp); self._stage(p)
            center = programs.Program(name="Audio Modeling Software Center", exe=r"C:\x\c.exe")
            proc = mock.Mock(); proc.poll.return_value = 0          # already exited
            with mock.patch.object(p, "run", return_value=subprocess.CompletedProcess([], 0)) as run:
                v.watch_program(p, center, proc)
            argv = [c[0][0] for c in run.call_args_list if c[0][0][0] != "reg"]
            self.assertEqual(len(argv), 1)
            self.assertEqual(argv[0][1:], ["--mode", "unattended", "--unattendedmodeui", "none"])
            swam = programs.Program(name="SWAM Violin", exe=r"C:\x\v.exe")
            with mock.patch.object(p, "run") as run:
                v.watch_program(p, swam, proc)                      # not the Center
            run.assert_not_called()
