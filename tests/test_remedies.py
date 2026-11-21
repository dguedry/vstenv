"""What to try when an installer fails: the technology is read from the file,
the remedies are a fixed menu, a remedy becomes arguments and environment for
one run (and a Windows version that is put back), and a failure explains
itself where the app recognises it."""
import json, tempfile, unittest
from pathlib import Path
from unittest import mock

from vstenv import remedies, recipes, programs


class FakePrefix:
    def __init__(self, root):
        self.path = Path(root); self.drive_c = self.path / "drive_c"; self.drive_c.mkdir(parents=True, exist_ok=True)
        self.values = {}
    def dll_overrides(self): return "winemenubuilder.exe=d;mshtml=d"
    def reg_add(self, key, name, value, kind="REG_SZ"): self.values.setdefault(key, {})[name] = value
    def reg_delete_value(self, key, name): self.values.get(key, {}).pop(name, None)
    def reg_query(self, key): return dict(self.values.get(key, {}))
    def to_win(self, host): return "C:\\" + str(Path(host).relative_to(self.drive_c)).replace("/", "\\")


def _exe(tmp, name, marker=b""):
    f = Path(tmp) / name; f.write_bytes(b"MZ" + b"\0" * 64 + marker); return f


class TechnologyTest(unittest.TestCase):
    def test_markers_and_msi(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(remedies.technology(_exe(tmp, "a.exe", b"Inno Setup Setup Data")), "inno")
            self.assertEqual(remedies.technology(_exe(tmp, "b.exe", b"Nullsoft Install System")), "nsis")
            self.assertEqual(remedies.technology(_exe(tmp, "c.exe", b"BitRock InstallBuilder")), "installbuilder")
            self.assertEqual(remedies.technology(_exe(tmp, "d.exe", b"__TAURI_INTERNALS__")), "tauri")
            self.assertEqual(remedies.technology(_exe(tmp, "e.exe")), "unknown")
            self.assertEqual(remedies.technology(Path(tmp) / "x.msi"), "msi")
            self.assertEqual(remedies.technology(Path(tmp) / "missing.exe"), "unknown")


class MenuTest(unittest.TestCase):
    def test_options_fit_the_technology_and_put_suggestions_first(self):
        ids = [o[0] for o in remedies.options("unknown")]
        self.assertNotIn("silent", ids)                    # no documented arguments for an unknown setup
        self.assertIn("silent", [o[0] for o in remedies.options("inno")])
        opts = remedies.options("tauri", 0, "")
        self.assertEqual(opts[0][0], "as_program"); self.assertTrue(opts[0][4])
        opts = remedies.options("qtifw", 1, "Unknown exception caught.")
        self.assertEqual(opts[0][0], "disable_netprofm")

    def test_every_remedy_is_within_the_installs_knobs(self):
        for rid, lab, when, settings in remedies.REMEDIES:
            self.assertTrue(lab and when)
            for k, v in settings.items():
                self.assertIn(k, recipes.KNOBS["installs"]); self.assertIn(v, recipes.KNOBS["installs"][k])
        self.assertEqual(remedies.label({"silent": "on"}), "Run it unattended")
        self.assertEqual(remedies.label({"winver": "win11"}), "winver=win11")

    def test_diagnosis_reads_the_known_signatures(self):
        d = remedies.diagnose("unknown", 53, "err:module:import_dll Library d3d9.dll (which is needed by L\"x.exe\") not found")
        self.assertTrue(any("d3d9.dll" in line for line in d))
        self.assertTrue(any("Tauri" in line for line in remedies.diagnose("tauri", 0, "")))
        self.assertTrue(any("1602" in line for line in remedies.diagnose("inno", 1602, "")))
        self.assertEqual(remedies.diagnose("inno", 1, ""), [])


class PrepareTest(unittest.TestCase):
    def test_arguments_environment_and_windows_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = FakePrefix(tmp); f = _exe(tmp, "Setup.exe", b"Inno Setup")
            args, env, cleanup = remedies.prepare(p, f, {"silent": "on", "rendering": "gdi", "disable_dll": "netprofm", "winver": "win8"}, "inno")
            self.assertEqual(args, ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"])
            self.assertEqual(env["WINEDLLOVERRIDES"], "winemenubuilder.exe=d;mshtml=d;d3d11,dxgi=d;netprofm=d")   # the prefix's own stay
            self.assertEqual(p.values[remedies.winver_key("Setup.exe")]["Version"], "win8")
            cleanup()
            self.assertNotIn("Version", p.values[remedies.winver_key("Setup.exe")])          # put back after the run
            args, env, _ = remedies.prepare(p, f, {"silent": "on"}, "unknown")
            self.assertEqual((args, env), ([], {}))                                         # nothing documented: nothing added

    def test_install_as_program_copies_and_registers(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = FakePrefix(tmp); f = _exe(tmp, "Some_Hub.exe", b"__TAURI_INTERNALS__")
            exe = remedies.install_as_program(p, f)
            self.assertTrue((p.drive_c / "Program Files/Some Hub/Some_Hub.exe").exists())
            self.assertTrue(exe.endswith("\\Some Hub\\Some_Hub.exe"))
            key = next(k for k in p.values if "Uninstall" in k)
            self.assertEqual(p.values[key]["DisplayName"], "Some Hub"); self.assertEqual(p.values[key]["DisplayIcon"], exe)


class InstallsRecipeTest(unittest.TestCase):
    def test_install_settings_match_by_name_or_glob_choice_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = FakePrefix(tmp)
            t = Path(tmp) / "fetched.json"; t.write_text(json.dumps({"installs": {"Serum2_Setup_*.exe": {"silent": "on", "why": "screens never start"}}}))
            with mock.patch.object(recipes, "FETCHED", t):
                self.assertEqual(recipes.install_settings(p, "serum2_setup_2.1.5.exe"), ({"silent": "on"}, "recipe"))
                self.assertEqual(recipes.install_settings(p, "Other.exe"), ({}, ""))
                recipes.set_choice(p, "installs", "Serum2_Setup_2.1.5.exe", winver="win8")
                self.assertEqual(recipes.install_settings(p, "Serum2_Setup_2.1.5.exe"), ({"winver": "win8"}, "you"))

    def test_failure_share_is_labelled_install_failure_and_carries_the_output(self):
        with mock.patch.object(recipes, "environment", return_value={"vstenv": "0.1.0", "wine": "w", "dxvk": "-", "gpu": "G", "session": "s", "distribution": "d"}):
            url = recipes.share_url(None, "installs", "Setup.exe", {}, "nothing happened", worked=False, extra="err:module:import_dll Library x.dll", diagnosis=["a DLL is missing"])
            title, body = recipes.share_text(None, "installs", "Setup.exe", {"silent": "on"}, "ok now", worked=True)
        self.assertIn("labels=install-failure", url); self.assertIn("import_dll", url); self.assertIn("reading", url)
        self.assertTrue(title.startswith("Install fix: Setup.exe"))
        self.assertEqual(json.loads(body[body.index("```json") + 7:body.index("```", body.index("```json") + 7)])["installs"]["Setup.exe"]["silent"], "on")


    def test_a_fix_report_says_what_the_file_is_and_what_was_tried(self):
        with mock.patch.object(recipes, "environment", return_value={"vstenv": "0.1.0", "wine": "w", "dxvk": "-", "gpu": "G", "session": "s", "distribution": "d"}):
            title, body = recipes.share_text(None, "installs", "LicenseSupport.exe", {"as_program": "on"}, "", worked=True,
                                             facts=["size: 12 bytes", "company: Acme"], history=["2026-11-21 10:00:00 — as is: exit 1 after 3 s — a DLL is missing"])
            url = recipes.share_url(None, "installs", "S.exe", {"silent": "on"}, "x", extra="o" * 9000, facts=["a"] * 8, history=["b" * 150] * 6)
        self.assertIn("**The file**\n- size: 12 bytes\n- company: Acme", body)
        self.assertIn("**Attempts so far**\n- 2026-11-21 10:00:00 — as is: exit 1", body)
        self.assertIn("without running it", body)      # as_program cannot fail: the report says so
        self.assertLess(len(url), 8000)                 # GitHub refuses a longer URL
        # the recipe block is still what the table takes
        self.assertEqual(json.loads(body[body.index("```json") + 7:body.index("```", body.index("```json") + 7)])["installs"]["LicenseSupport.exe"]["as_program"], "on")


class FileFactsTest(unittest.TestCase):
    @staticmethod
    def _exe(strings: dict) -> bytes:
        """A blob with a version resource shaped as the real thing: the fixed
        block, then String records (wLength, wValueLength, wType=1, key, value)."""
        import struct
        u = lambda t: t.encode("utf-16-le")
        b = b"MZ" + b"\0" * 62 + u("VS_VERSION_INFO") + b"\0\0\0\0" + struct.pack("<IIII", 0xFEEF04BD, 0x10000, 0x00020001, 0x00030000)
        b += b"\0" * 40
        for k, v in strings.items():
            rec = u(k) + b"\0\0"
            if (6 + len(rec)) % 4: rec += b"\0\0"
            rec += u(v) + b"\0\0"
            rec = struct.pack("<HHH", 6 + len(rec), len(v) + 1, 1) + rec
            if len(rec) % 4: rec += b"\0\0"
            b += rec
        # a program's own table of the key names, as Chromium carries: must not be read as values
        return u("CompanyName\0CompanyShortName\0ProductName\0") + b

    def test_facts_name_the_maker_and_fingerprint_the_file(self):
        from vstenv import pe
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "LicenseSupport.exe"; f.write_bytes(self._exe({"CompanyName": "Acme Audio", "ProductName": "License Support", "FileDescription": "Acme licence service setup"}))
            self.assertEqual(pe.strings(f), {"CompanyName": "Acme Audio", "ProductName": "License Support", "FileDescription": "Acme licence service setup"})
            facts = remedies.file_facts(f)
            self.assertIn("company: Acme Audio", facts); self.assertIn("product: License Support", facts)
            self.assertIn("file version: 2.1.3", facts)
            self.assertTrue(facts[0].startswith("size: ")); self.assertRegex(facts[-1], r"^sha256: [0-9a-f]{16}$")
            self.assertEqual(remedies.file_facts(Path(tmp) / "missing.exe"), [])
            (Path(tmp) / "plain.exe").write_bytes(b"MZ nothing here"); self.assertEqual(pe.strings(Path(tmp) / "plain.exe"), {})

    def test_history_lists_the_attempts_at_one_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows = [{"time": "t1", "file": "A.exe", "remedy": {}, "exit": 1, "seconds": 3.0, "diagnosis": ["a DLL is missing"]},
                    {"time": "t2", "file": "B.exe", "remedy": {}, "exit": 0, "seconds": 9.0},
                    {"time": "t3", "file": "A.exe", "remedy": {"as_program": "on"}, "exit": 0, "seconds": 0, "as_program": "C:\\x\\A.exe"}]
            (Path(tmp) / programs.JOURNAL).write_text("\n".join(json.dumps(r) for r in rows) + "\nnot json\n")
            with mock.patch("vstenv.paths.LOGS", Path(tmp)):
                self.assertEqual(programs.history("A.exe"), ["t1 — as is: exit 1 after 3.0 s — a DLL is missing",
                                                             "t3 — as_program=on: copied in as a program (not run)"])
                self.assertEqual(programs.history("C.exe"), [])


class JournalTest(unittest.TestCase):
    def test_install_keeps_output_and_a_journal_line(self):
        src = Path("vstenv/programs.py").read_text()
        i = src.index("def install(p: Prefix, installer"); j = src.find("\ndef ", i + 10)
        body = src[i:j if j != -1 else len(src)]           # install() is the file's last function
        self.assertIn("stderr_to=log", body); self.assertIn("_journal(entry)", body)
        self.assertIn("recipes.install_settings(p, installer.name)", body)      # a kept remedy applies by itself
        self.assertIn("remedies.diagnose(", body)
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch("vstenv.paths.LOGS", Path(tmp)):
                programs._journal({"file": "a.exe", "exit": 53}); programs._journal({"file": "b.exe", "exit": 0})
                j = programs.journal()
            self.assertEqual([e["file"] for e in j], ["b.exe", "a.exe"])


if __name__ == "__main__":
    unittest.main()
