"""Wine runs on the host: inside Flatpak every Wine command is wrapped in
flatpak-spawn --host with only Wine's own variables; outside it runs directly."""
import unittest
from pathlib import Path
from vstenv import host
from vstenv.wine import Prefix, WineBuild

class HostRunnerTest(unittest.TestCase):
    def setUp(self):
        self._old = host.FLATPAK_INFO
    def tearDown(self):
        host.FLATPAK_INFO = self._old

    def test_wrap_outside_flatpak_is_identity(self):
        host.FLATPAK_INFO = Path("/nonexistent/.flatpak-info")
        self.assertEqual(host.wrap(["/w/bin/wine", "cmd"], {"WINEPREFIX": "/p"}), ["/w/bin/wine", "cmd"])

    def test_wrap_inside_flatpak(self):
        host.FLATPAK_INFO = Path("/etc/hostname")          # any existing file stands in for /.flatpak-info
        argv = host.wrap(["/w/bin/wine", "cmd", "/c", "echo"], {"WINEPREFIX": "/p", "WINEFSYNC": "1"}, cwd="/tmp")
        self.assertEqual(argv[:2], ["flatpak-spawn", "--host"])
        self.assertIn("--directory=/tmp", argv)
        self.assertIn("--env=WINEPREFIX=/p", argv); self.assertIn("--env=WINEFSYNC=1", argv)
        self.assertEqual(argv[argv.index("--") + 1:], ["/w/bin/wine", "cmd", "/c", "echo"])

    def test_electron_run_as_node_never_reaches_wine(self):
        host.FLATPAK_INFO = Path("/etc/hostname")
        self.assertIn("--unset-env=ELECTRON_RUN_AS_NODE", host.wrap(["/w/bin/wine"], {}))
        host.FLATPAK_INFO = Path("/nonexistent/.flatpak-info")
        import os
        old = os.environ.get("ELECTRON_RUN_AS_NODE"); os.environ["ELECTRON_RUN_AS_NODE"] = "1"
        try: self.assertNotIn("ELECTRON_RUN_AS_NODE", host._kw({"WINEPREFIX": "/p"}, None, {})["env"])
        finally:
            if old is None: os.environ.pop("ELECTRON_RUN_AS_NODE", None)
            else: os.environ["ELECTRON_RUN_AS_NODE"] = old

    def test_wine_env_is_only_wines_variables(self):
        p = Prefix(Path("/p"), WineBuild(Path("/w")))
        e = p.wine_env({"WINEDEBUG": "+seh"})
        self.assertEqual(set(e), {"WINEPREFIX", "WINEFSYNC", "WINEDEBUG", "WINEDLLOVERRIDES", "WINEARCH"})
        self.assertEqual(e["WINEDEBUG"], "+seh"); self.assertEqual(e["WINEPREFIX"], "/p")
        self.assertNotIn("PATH", e)                        # the host session keeps its own PATH

    def test_processes_parses_host_scan(self):
        p = Prefix(Path("/p"), WineBuild(Path("/w")))
        old = host.sh
        host.sh = lambda script, env=None, timeout=60: "123\tC:\\Program Files\\x\\NTKDaemon.exe\n124\t/w/bin/wineserver\nbad line\n"
        try:
            self.assertEqual(p.processes(), [(123, "C:\\Program Files\\x\\NTKDaemon.exe"), (124, "/w/bin/wineserver")])
            self.assertEqual(p.processes("ntkdaemon.exe"), [(123, "C:\\Program Files\\x\\NTKDaemon.exe")])
            self.assertTrue(p.is_running("NTKDaemon.exe"))
        finally:
            host.sh = old

    def test_proc_scan_script_runs_locally(self):
        # the same sh script that runs on the host works locally (source installs)
        import os, subprocess
        p = Prefix(Path("/definitely/not/a/prefix"), WineBuild(Path("/w")))
        out = subprocess.run(["sh", "-c", p.PROC_SCAN], env={**os.environ, "VSTENV_PREFIX": str(p.path)},
                             capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0); self.assertEqual(out.stdout.strip(), "")

if __name__ == "__main__":
    unittest.main()
