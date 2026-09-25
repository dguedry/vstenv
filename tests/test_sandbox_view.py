"""The Flatpak sandbox must see every host path Wine reaches through the prefix,
because the prefix's wineserver (shared with host DAWs) opens files for every
client. sandbox_gaps() lists what a given set of grants leaves invisible."""
import os, tempfile, unittest
from pathlib import Path
from vstenv import prefixes

class FakePrefix:
    def __init__(self, path: Path, user="me"):
        self.path = Path(path); self._user = user
    @property
    def drive_c(self): return self.path / "drive_c"
    @property
    def user_dir(self): return self.drive_c / "users" / self._user

class SandboxViewTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); root = Path(self.tmp.name)
        self.home = root / "home" / "me"
        (self.home / "Documents" / "Native Instruments").mkdir(parents=True)
        (self.home / "Music").mkdir()
        self.p = FakePrefix(self.home / ".var/app/io.github.dguedry.nilinux/data/nilinux/prefix")
        self.p.user_dir.mkdir(parents=True)
        (self.p.user_dir / "AppData").mkdir()
        os.symlink(self.home / "Documents", self.p.user_dir / "Documents")
        os.symlink(self.home / "Music", self.p.user_dir / "Music")
        # a library on another disk and one inside the prefix, both registered for Kontakt
        (self.p.path / "system.reg").write_text(
            '[Software\\\\Native Instruments\\\\Some Library] 1\n"ContentDir"="Z:\\\\mnt\\\\samples\\\\Some Library"\n'
            '[Software\\\\Native Instruments\\\\Kontakt 8] 1\n"ContentDir"="C:\\\\Program Files\\\\Common Files\\\\Native Instruments\\\\Kontakt 8"\n')

    def tearDown(self): self.tmp.cleanup()

    def test_host_paths_include_user_folder_links_and_foreign_content_dirs(self):
        paths = prefixes.host_paths_wine_opens(self.p)
        self.assertEqual(paths["C:\\users\\me\\Documents"], self.home / "Documents")
        self.assertEqual(paths["C:\\users\\me\\Music"], self.home / "Music")
        self.assertEqual(paths["Z:\\mnt\\samples\\Some Library"], Path("/mnt/samples/Some Library"))
        self.assertFalse(any("Kontakt 8" in k for k in paths))     # inside the prefix: always visible

    def test_narrow_grants_leave_gaps(self):
        # no "/tmp" here: the fixture home lives under the temp dir, which /tmp would cover
        grants = ["~/.vst3:create", "xdg-run/wine:create", "~/.local/share/yabridge:create"]
        gaps = prefixes.sandbox_gaps(self.p, grants, home=self.home)
        self.assertEqual(len(gaps), 3)
        self.assertTrue(any("Documents" in g for g in gaps))
        self.assertTrue(any("Music" in g for g in gaps))
        self.assertTrue(any("samples" in g for g in gaps))

    def test_xdg_and_home_grants(self):
        self.assertEqual(len(prefixes.sandbox_gaps(self.p, ["xdg-documents", "xdg-music"], home=self.home)), 1)   # the /mnt library
        self.assertEqual(len(prefixes.sandbox_gaps(self.p, ["home"], home=self.home)), 1)
        self.assertEqual(prefixes.sandbox_gaps(self.p, ["home", "/mnt/samples:ro"], home=self.home), [])

    def test_host_grant_sees_everything(self):
        self.assertEqual(prefixes.sandbox_gaps(self.p, ["/tmp", "host"], home=self.home), [])

    def test_flatpak_info_parsing_and_absence(self):
        info = Path(self.tmp.name) / "flatpak-info"
        info.write_text("[Application]\nname=x\n\n[Context]\nshared=network;\nfilesystems=/tmp;host;~/.vst3:create;\n")
        self.assertEqual(prefixes.flatpak_filesystems(info), ["/tmp", "host", "~/.vst3:create"])
        self.assertIsNone(prefixes.flatpak_filesystems(Path(self.tmp.name) / "missing"))

if __name__ == "__main__": unittest.main()
