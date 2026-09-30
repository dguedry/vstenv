import struct, tempfile, unittest
from pathlib import Path
from vstenv import programs
from vstenv.wine import Prefix, WineBuild

SYSTEM_REG = r'''WINE REGISTRY Version 2
;; All keys relative to \\Machine

[Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\a401809f] 1789339365
"DisplayIcon"="C:\\Program Files\\IK Multimedia\\IK Product Manager\\IK Product Manager.exe,0"
"DisplayName"="IK Product Manager"
"DisplayVersion"="1.1.15"
"Publisher"="IK Multimedia"
"UninstallString"="\"C:\\Program Files\\IK Multimedia\\IK Product Manager\\Uninstall IK Product Manager.exe\" /allusers"

[Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\{e5426377}] 1788910283
"DisplayName"="Native Instruments Dirt"
"DisplayVersion"="1.3.7.0"
"InstallLocation"="C:\\Program Files\\Native Instruments\\Dirt"
"Publisher"="Native Instruments"
"SystemComponent"=dword:00000001

[Software\\Wow6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\Native Instruments Dirt] 1789080509
"DisplayName"="Native Instruments Dirt"
"DisplayVersion"="1.3.7.0"
"InstallLocation"="C:\\Program Files\\Native Instruments\\Dirt"
"Publisher"="Native Instruments"
"UninstallString"="cmd.exe /C del \"C:\\x.json\" && start \"\" \"C:\\ProgramData\\Dirt Setup PC.exe\""

[Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\{vc}] 1
"DisplayName"="Microsoft Visual C++ 2022 Redistributable"

[Software\\Native Instruments\\Kontakt 8] 1
"ContentVersion"="8.12.1"
"InstallDir"="C:\\Program Files\\Native Instruments\\Kontakt 8"

[Software\\Native Instruments\\Kontakt 8\\Sub] 1
"X"="y"

[Software\\Native Instruments\\Scarbee Mark I] 1
"ContentDir"="C:\\Users\\Public\\Documents\\Scarbee"
'''

def make_lnk(target: str, name: str, workdir: str, args: str) -> bytes:
    """A minimal Shell Link the way Wine writes them: LinkInfo with LocalBasePath,
    then unicode Name / WorkingDir / Arguments string data."""
    flags = 0x02 | 0x04 | 0x10 | 0x20 | 0x80          # HasLinkInfo, HasName, HasWorkingDir, HasArguments, IsUnicode
    header = b"L\0\0\0" + b"\x01\x14\x02\x00\x00\x00\x00\x00\xc0\x00\x00\x00\x00\x00\x00\x46" + struct.pack("<I", flags) + b"\0" * (0x4C - 0x18)
    lbp = target.encode("cp1252") + b"\0"; cps = b"\0"
    hsize = 0x1C
    lbp_off = hsize; cps_off = lbp_off + len(lbp)
    size = cps_off + len(cps)
    linkinfo = struct.pack("<7I", size, hsize, 0x01, 0, lbp_off, 0, cps_off) + lbp + cps
    def s(x): e = x.encode("utf-16-le"); return struct.pack("<H", len(x)) + e
    return header + linkinfo + s(name) + s(workdir) + s(args)

class RegTest(unittest.TestCase):
    def test_sections_and_escapes(self):
        secs = programs.reg_sections(SYSTEM_REG)
        k = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\a401809f"
        self.assertEqual(secs[k]["DisplayName"], "IK Product Manager")
        self.assertEqual(secs[k]["UninstallString"], r'"C:\Program Files\IK Multimedia\IK Product Manager\Uninstall IK Product Manager.exe" /allusers')
        self.assertEqual(secs[r"Software\Microsoft\Windows\CurrentVersion\Uninstall\{e5426377}"]["SystemComponent"], "1")

class LnkTest(unittest.TestCase):
    def test_parse(self):
        b = make_lnk(r"C:\Program Files\X\X.exe", "X", r"C:\Program Files\X", "--flag")
        info = programs.parse_lnk(b)
        self.assertEqual(info["target"], r"C:\Program Files\X\X.exe")
        self.assertEqual(info["name"], "X"); self.assertEqual(info["workdir"], r"C:\Program Files\X"); self.assertEqual(info["args"], "--flag")
    def test_wine_trailing_nul_in_strings(self):
        b = make_lnk("C:\\P\\X.exe", "X\0", "C:\\P\0", "\0")     # Wine counts the terminator in the length
        info = programs.parse_lnk(b)
        self.assertEqual((info["name"], info["workdir"], info["args"]), ("X", r"C:\P", ""))
    def test_garbage(self):
        self.assertEqual(programs.parse_lnk(b"nope")["target"], "")

class InstalledTest(unittest.TestCase):
    def test_merge_registry_and_shortcuts(self):
        with tempfile.TemporaryDirectory() as d:
            p = Prefix(Path(d), WineBuild(Path("/w")))
            (p.path / "system.reg").write_text(SYSTEM_REG)
            sm = p.drive_c / "ProgramData/Microsoft/Windows/Start Menu/Programs"; sm.mkdir(parents=True)
            (sm / "IK Product Manager.lnk").write_bytes(make_lnk(r"C:\Program Files\IK Multimedia\IK Product Manager\IK Product Manager.exe", "IK Product Manager", r"C:\Program Files\IK Multimedia\IK Product Manager", ""))
            (sm / "Uninstall Foo.lnk").write_bytes(make_lnk(r"C:\Program Files\Foo\unins000.exe", "Uninstall Foo", "", ""))
            (sm / "Standalone.lnk").write_bytes(make_lnk(r"C:\Program Files\Standalone\Standalone.exe", "Standalone", "", "-x"))
            (p.drive_c / "users/me").mkdir(parents=True)
            k8 = p.drive_c / "Program Files/Native Instruments/Kontakt 8"; k8.mkdir(parents=True)
            (k8 / "Kontakt 8.exe").write_bytes(b"x" * 100); (k8 / "Kontakt 8 Setup PC.exe").write_bytes(b"x" * 500)
            progs = {x.name: x for x in programs.installed(p)}
            self.assertEqual(set(progs), {"IK Product Manager", "Native Instruments Dirt", "Standalone", "Kontakt 8"})   # VC++ skipped, uninstaller shortcut skipped, Scarbee has no exe
            self.assertEqual(progs["Kontakt 8"].exe, r"C:\Program Files\Native Instruments\Kontakt 8\Kontakt 8.exe")   # setup exe rejected although larger
            self.assertEqual(progs["Kontakt 8"].sources, ["ni"]); self.assertEqual(progs["Kontakt 8"].version, "8.12.1")
            ik = progs["IK Product Manager"]
            self.assertEqual(ik.version, "1.1.15"); self.assertEqual(sorted(ik.sources), ["registry", "shortcut"])
            self.assertTrue(ik.exe.endswith("IK Product Manager.exe")); self.assertTrue(ik.uninstall.startswith('"C:'))
            self.assertEqual(ik.install_dir, r"C:\Program Files\IK Multimedia\IK Product Manager")   # derived from the launcher: no InstallLocation in the registry
            dirt = progs["Native Instruments Dirt"]
            self.assertEqual(dirt.vendor, "ni"); self.assertTrue(dirt.uninstall.startswith("cmd.exe")); self.assertEqual(dirt.exe, "")   # no install dir on disk
            self.assertEqual(progs["Standalone"].args, "-x"); self.assertEqual(progs["Standalone"].sources, ["shortcut"])
            self.assertEqual(programs.find(p, "ik product").name, "IK Product Manager")
            with self.assertRaises(LookupError): programs.find(p, "nothing")

class UninstallArgvTest(unittest.TestCase):
    def test_forms(self):
        self.assertEqual(programs.uninstall_argv(r'"C:\P F\u.exe" /allusers /S'), [r"C:\P F\u.exe", "/allusers", "/S"])
        self.assertEqual(programs.uninstall_argv("MsiExec.exe /X{ABC}"), ["MsiExec.exe", "/X{ABC}"])
        self.assertEqual(programs.uninstall_argv('cmd.exe /C del "x" && start "" "y.exe"')[:2], ["cmd", "/c"])
        self.assertEqual(programs.uninstall_argv(r"C:\x\unins.exe /silent"), [r"C:\x\unins.exe", "/silent"])
        self.assertEqual(programs.uninstall_argv(""), [])

if __name__ == "__main__":
    unittest.main()


class InstallerAutoLaunchTest(unittest.TestCase):
    """An installer's "run it now" starts the program before its quirks are
    applied; that instance must be replaced by one started with the fixes."""
    def _install(self, running, already_present=False):
        import subprocess, tempfile
        from unittest import mock
        from vstenv import quirks
        from vstenv.wine import Prefix, WineBuild
        with tempfile.TemporaryDirectory() as tmp:
            p = Prefix(Path(tmp) / "prefix", WineBuild(Path(tmp) / "wine"))
            inst = Path(tmp) / "setup.exe"; inst.write_bytes(b"MZ")
            prog = programs.Program(name="IK Product Manager", exe=r"C:\Program Files\IK\IK Product Manager.exe", install_dir=r"C:\Program Files\IK")
            before = [prog] if already_present else []      # installed() before the installer ran, then after
            with mock.patch.object(p, "run", return_value=subprocess.CompletedProcess([], 0)), \
                 mock.patch.object(p, "processes", return_value=[]), \
                 mock.patch.object(programs, "installed", side_effect=[before, [prog]]), \
                 mock.patch.object(quirks, "apply", return_value=["os-info patched"]) as apply, \
                 mock.patch.object(quirks, "launch_args", return_value=["--disable-gpu"]), \
                 mock.patch.object(p, "is_running", return_value=running), \
                 mock.patch.object(p, "kill_exe") as kill, mock.patch.object(programs, "run") as run:
                programs.install(p, inst)
                return kill, run, apply
    def test_auto_started_instance_is_replaced(self):
        kill, run, _ = self._install(running=True)
        kill.assert_called_once_with("IK Product Manager.exe"); self.assertEqual(run.call_count, 1)
    def test_nothing_started_means_nothing_restarted(self):
        kill, run, _ = self._install(running=False)
        kill.assert_not_called(); run.assert_not_called()
    def test_programs_already_in_the_prefix_are_left_alone(self):
        # installing a third-party plugin must not re-run (and re-report) another vendor's quirks
        kill, run, apply = self._install(running=True, already_present=True)
        apply.assert_not_called(); kill.assert_not_called(); run.assert_not_called()


class InstallerHelperWaitTest(unittest.TestCase):
    """A wrapper installer returns while the setup it unpacked into Temp is still
    running (iZotope); the app must wait for that helper before bridging, but not
    for a product the installer offered to run."""
    def _run(self, scans):
        # scans is the sequence of process snapshots install() will see; once it
        # runs out, the last snapshot repeats (so an extra scan for orphan cleanup
        # does not raise StopIteration and just sees a quiet prefix).
        import subprocess, tempfile
        from unittest import mock
        from vstenv.wine import Prefix, WineBuild
        seq = list(scans); calls = {"n": 0}
        def fake_processes(exe_name=None):
            i = min(calls["n"], len(seq) - 1); calls["n"] += 1; return seq[i]
        with tempfile.TemporaryDirectory() as tmp:
            p = Prefix(Path(tmp) / "prefix", WineBuild(Path(tmp) / "wine"))
            inst = Path(tmp) / "iZotope_Ozone.exe"; inst.write_bytes(b"MZ")
            with mock.patch.object(p, "run", return_value=subprocess.CompletedProcess([], 0)), \
                 mock.patch.object(p, "processes", side_effect=fake_processes), \
                 mock.patch.object(p, "kill_respawning", return_value=1) as killr, \
                 mock.patch.object(programs, "installed", return_value=[]), \
                 mock.patch.object(programs.time, "sleep"):
                programs.install(p, inst)
                return calls["n"], killr.call_count
    def test_waits_for_the_setup_the_wrapper_started(self):
        services = [(10, r"C:\windows\system32\services.exe"), (11, r"C:\windows\system32\explorer.exe")]
        helper = (55, r"C:\users\me\Temp\{1234}\Ozone 9 Advanced Setup.exe")
        # before; helper still running (poll 1,2); gone (poll 3); then the orphan scan sees a quiet prefix
        scans = [services, services + [helper], services + [helper], services, services]
        n, killed = self._run(scans)
        self.assertGreaterEqual(n, 4); self.assertEqual(killed, 0)      # nothing respawning -> no cleanup
    def test_a_crash_looping_manager_left_by_the_installer_is_stopped(self):
        services = [(10, r"C:\windows\system32\services.exe")]
        # a Product Portal that was NOT there before the install appears afterwards -> cleaned up
        portal = (77, r"C:\Program Files\iZotope\Product Portal\x64\iZotope Product Portal.exe --type=renderer")
        n, killed = self._run([services, services + [portal]])
        self.assertEqual(killed, 1)
    def test_a_service_that_was_already_running_is_ignored(self):
        # a renderer present BEFORE the install is not treated as an orphan of it
        old = [(10, r"C:\windows\system32\services.exe"), (20, r"C:\Program Files\x\App.exe --type=renderer")]
        n, killed = self._run([old, old])
        self.assertEqual(killed, 0)


class ShortcutFilterTest(unittest.TestCase):
    """Uninstall and setup shortcuts are not launchers, and MSI-written shortcuts
    carry Wine's 8.3 short names that only Wine can resolve."""
    def _prefix(self, tmp):
        p = Prefix(Path(tmp) / "prefix", WineBuild(Path(tmp) / "wine")); (p.drive_c / "users/me").mkdir(parents=True)
        self.menu = p.user_dir / "AppData/Roaming/Microsoft/Windows/Start Menu/Programs/Steinberg"; self.menu.mkdir(parents=True)
        return p
    def _lnk(self, target, args="", workdir=""):
        from unittest import mock
        return mock.patch.object(programs, "parse_lnk", side_effect=lambda b: {"target": b.decode().split("|")[0], "args": b.decode().split("|")[1], "workdir": b.decode().split("|")[2], "name": ""})
    def test_uninstall_and_setup_shortcuts_are_skipped_and_short_paths_resolved(self):
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp:
            p = self._prefix(tmp)
            (self.menu / "HALion Sonic.lnk").write_bytes(b"C:\\PROG~FBU\\STEI~PSS\\HALI~04O\\HALI~SYJ.EXE||C:\\Program Files\\Steinberg\\HALion Sonic\\")
            (self.menu / "Uninstall HALionSonic.lnk").write_bytes(b"C:\\windows\\syswow64\\msiexec.exe|/x {69043884-EB60-4C9A-9C41-3303C319E1A8}|")
            (self.menu / "Steinberg built-in ASIO Driver Setup (x64).lnk").write_bytes(b"C:\\PROG~FBU\\STEI~PSS\\Asio\\ASIO~L1I.EXE||")
            (self.menu / "Remove Something.lnk").write_bytes(b"C:\\Program Files\\X\\x.exe|--uninstall|")
            def run(argv, **kw):
                import subprocess
                self.assertEqual(argv[:2], ["winepath", "-l"]); self.assertEqual(argv[2:], ["C:\\PROG~FBU\\STEI~PSS\\HALI~04O\\HALI~SYJ.EXE"])
                return subprocess.CompletedProcess(argv, 0, stdout="C:\\Program Files\\Steinberg\\HALion Sonic\\HALion Sonic.exe\n", stderr="")
            with self._lnk(None), mock.patch.object(p, "run", side_effect=run):
                progs = programs.shortcut_programs(p)
            self.assertEqual([(x.name, x.exe) for x in progs], [("HALion Sonic", "C:\\Program Files\\Steinberg\\HALion Sonic\\HALion Sonic.exe")])
            self.assertEqual(progs[0].install_dir, "C:\\Program Files\\Steinberg\\HALion Sonic")
    def test_no_short_paths_means_no_winepath_call(self):
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp:
            p = self._prefix(tmp)
            (self.menu / "Thing.lnk").write_bytes(b"C:\\Program Files\\Thing\\thing.exe||")
            with self._lnk(None), mock.patch.object(p, "run") as run:
                self.assertEqual(len(programs.shortcut_programs(p)), 1); run.assert_not_called()


class MergeSlashStyleTest(unittest.TestCase):
    """A registry entry written with forward slashes (BitRock) must merge with the
    Start Menu shortcut for the same exe instead of producing two programs."""
    def test_same_exe_different_slashes_is_one_program(self):
        import tempfile
        from unittest import mock
        from vstenv.wine import Prefix, WineBuild
        with tempfile.TemporaryDirectory() as tmp:
            p = Prefix(Path(tmp) / "prefix", WineBuild(Path(tmp) / "wine"))
            (p.drive_c / "users/me").mkdir(parents=True)
            reg = programs.Program(name="SWAM Violin", exe=r"C:\Program Files/Audio Modeling/SWAM Violin\SWAM Violin 3.exe",
                                   install_dir=r"C:\Program Files/Audio Modeling/SWAM Violin", sources=["registry"])
            link = programs.Program(name="SWAM Violin 3", exe=r"C:\Program Files\Audio Modeling\SWAM Violin\SWAM Violin 3.exe",
                                    sources=["shortcut"])
            with mock.patch.object(programs, "registry_programs", return_value=[reg]), \
                 mock.patch.object(programs, "shortcut_programs", return_value=[link]), \
                 mock.patch("vstenv.vendors.all", return_value=[]):
                out = programs.installed(p)
            names = [x.name for x in out if "SWAM" in x.name]
            self.assertEqual(names, ["SWAM Violin"])
