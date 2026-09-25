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
