"""Desktop menu entries: one per runnable program, started through `vstenv run`,
stale ones removed, nothing that is not ours touched."""
import tempfile, unittest
from pathlib import Path
from unittest import mock
from vstenv import menu, programs, host
from vstenv.programs import Program
from vstenv.wine import Prefix, WineBuild

class MenuTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); root = Path(self.tmp.name)
        self.p = Prefix(root / "prefix", WineBuild(root / "wine")); (self.p.drive_c / "Program Files/K").mkdir(parents=True)
        self.apps = root / "applications"; self.icons = root / "icons"
        self.progs = [Program(name="Kontakt 8", publisher="Native Instruments", exe=r"C:\Program Files\K\Kontakt 8.exe", install_dir=r"C:\Program Files\K"),
                      Program(name="Only Uninstall", uninstall="x.exe"),
                      Program(name="Amp \"Live\" 2", publisher="Someone", exe=r"C:\Program Files\K\amp.exe")]
        self.patches = [mock.patch.object(menu, "APPS", self.apps), mock.patch.object(menu, "ICONS", self.icons),
                        mock.patch.object(programs, "installed", lambda p: list(self.progs)),
                        mock.patch.object(menu.shutil, "which", return_value=None)]
        for x in self.patches: x.start()

    def tearDown(self):
        for x in self.patches: x.stop()
        self.tmp.cleanup()

    def test_sync_writes_one_entry_per_runnable_program(self):
        res = menu.sync(self.p)
        self.assertEqual((res["programs"], res["written"]), (2, 2))
        f = self.apps / "io.github.dguedry.vstenv.program.kontakt-8.desktop"
        body = f.read_text()
        self.assertIn("Name=Kontakt 8\n", body); self.assertIn("Native Instruments", body)
        with mock.patch.object(host, "in_flatpak", return_value=False): self.assertEqual(menu.exec_line("Kontakt 8"), 'vstenv run "Kontakt 8"')
        with mock.patch.object(host, "in_flatpak", return_value=True): self.assertIn("flatpak run --command=vstenv io.github.dguedry.vstenv run", menu.exec_line("Kontakt 8"))
        self.assertIn('Exec=', body); self.assertIn("Icon=io.github.dguedry.vstenv", body)     # no real exe: app icon
        quoted = (self.apps / "io.github.dguedry.vstenv.program.amp-live-2.desktop").read_text()
        self.assertIn('run "Amp \\"Live\\" 2"', quoted)
        self.assertEqual(menu.sync(self.p)["written"], 0, "idempotent")

    def test_stale_entries_go_and_foreign_files_stay(self):
        menu.sync(self.p)
        foreign = self.apps / "org.other.App.desktop"; foreign.write_text("[Desktop Entry]\n")
        self.progs.pop(0)
        res = menu.sync(self.p)
        self.assertEqual(res["removed"], 1)
        self.assertFalse((self.apps / "io.github.dguedry.vstenv.program.kontakt-8.desktop").exists())
        self.assertTrue(foreign.exists())
        self.assertEqual(menu.remove_all(), 1); self.assertEqual(menu.ours(), []); self.assertTrue(foreign.exists())

if __name__ == "__main__": unittest.main()
