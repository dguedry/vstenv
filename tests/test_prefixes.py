import os, tempfile, unittest
from pathlib import Path
from vstenv import prefixes

STATUS = """yabridge path: '/home/me/.local/share/yabridge'
VST3 location: '/home/me/.vst3/yabridge'
/home/me/.local/share/vstenv/prefix/drive_c/Program Files/Common Files/VST3/
  Kontakt 8.vst3 :: VST3, legacy, 64-bit, synced
/home/me/.var/app/io.github.dguedry.vstenv/data/vstenv/prefix/drive_c/Program Files/Common Files/VST3/
/home/me/.var/app/org.vstenv.Old/data/vstenv/prefix/drive_c/Program Files/VstPlugins/
/home/me/.wine-games/drive_c/Program Files/VstPlugins/
"""

class FakePrefix:
    def __init__(self, path): self.path = Path(path)

class PrefixesTest(unittest.TestCase):
    def test_yabridgectl_dirs_parses_only_directory_lines(self):
        dirs = prefixes.yabridgectl_dirs(STATUS)
        self.assertEqual(len(dirs), 4)
        self.assertTrue(all(d.startswith("/") and d.endswith("/") for d in dirs))

    def test_prefix_of_dir(self):
        self.assertEqual(prefixes.prefix_of_dir("/x/.local/share/vstenv/prefix/drive_c/Program Files/VST3/"),
                         Path("/x/.local/share/vstenv/prefix"))
        self.assertEqual(prefixes.prefix_of_dir("/x/.var/app/some.id/data/vstenv/prefix/drive_c"),
                         Path("/x/.var/app/some.id/data/vstenv/prefix"))
        self.assertIsNone(prefixes.prefix_of_dir("/x/.wine-games/drive_c/Program Files/VstPlugins/"))
        self.assertIsNone(prefixes.prefix_of_dir("/usr/lib/vst3"))

    def test_foreign_dirs_excludes_this_prefix_and_third_party_prefixes(self):
        p = FakePrefix("/home/me/.local/share/vstenv/prefix")
        foreign = prefixes.foreign_yabridge_dirs(p, STATUS)
        self.assertEqual(len(foreign), 2)
        self.assertTrue(all("io.github.dguedry.vstenv" in d or "org.vstenv.Old" in d for d in foreign))
        flat = FakePrefix("/home/me/.var/app/io.github.dguedry.vstenv/data/vstenv/prefix")
        foreign2 = prefixes.foreign_yabridge_dirs(flat, STATUS)
        self.assertEqual(len(foreign2), 2)
        self.assertTrue(all(".local/share" in d or "org.vstenv" in d for d in foreign2))

    def test_app_prefixes_finds_real_prefixes_only(self):
        with tempfile.TemporaryDirectory() as home:
            real = Path(home) / ".local/share/vstenv/prefix/drive_c/windows"; real.mkdir(parents=True)
            fake = Path(home) / ".var/app/x.y.z/data/vstenv/prefix"; fake.mkdir(parents=True)   # no drive_c/windows
            old_home, old_xdg = os.environ.get("HOME"), os.environ.pop("XDG_DATA_HOME", None)
            os.environ["HOME"] = home
            try:
                import importlib; from vstenv import paths; importlib.reload(paths)
                found = prefixes.app_prefixes()
            finally:
                os.environ["HOME"] = old_home
                if old_xdg: os.environ["XDG_DATA_HOME"] = old_xdg
                importlib.reload(paths)
            self.assertEqual([str(x) for x in found], [str(Path(home) / ".local/share/vstenv/prefix")])

    def test_port_owner_free_ports(self):
        # 3 ephemeral-range ports nobody listens on
        o = prefixes.port_owner((47001, 47002, 47003))
        self.assertEqual(o["busy"], [])
        self.assertIsNone(o["pid"])

    def test_port_owner_identifies_this_process(self):
        import socket
        with socket.socket() as sk:
            sk.bind(("127.0.0.1", 0)); sk.listen(1)
            port = sk.getsockname()[1]
            os.environ["WINEPREFIX"] = "/tmp/fake-prefix-for-test"
            o = prefixes.port_owner((port,))
        self.assertEqual(o["busy"], [port])
        self.assertEqual(o["pid"], os.getpid())
        # our own environ is read from /proc, which reflects the process start, so
        # the prefix is whatever we were started with; the pid is the real check
        self.assertIn("python", o["exe"].lower())

if __name__ == "__main__": unittest.main()
