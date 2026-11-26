"""wineasio: the stub is copied into system32 and registered once, through the prefix's runner."""
import subprocess, tempfile, unittest
from pathlib import Path
from unittest import mock
from vstenv import wineasio, winefixes
from vstenv.wine import Prefix, WineBuild

class WineAsioTest(unittest.TestCase):
    def _prefix(self, tmp, present=True):
        b = WineBuild(Path(tmp) / "wine-11.17-staging-amd64-wow64")
        p = Prefix(Path(tmp) / "prefix", b)
        winefixes.dest(b, wineasio.DLL).parent.mkdir(parents=True)
        winefixes.dest(b, wineasio.DLL).write_bytes(b"stub")
        (p.drive_c / "windows/system32").mkdir(parents=True)
        inst = {wineasio.DLL: "x", "x86_64-unix/wineasio.dll.so": "x"} if present else {}
        return p, mock.patch.object(winefixes, "installed", return_value=inst)

    def test_not_in_the_build_does_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            p, inst = self._prefix(d, present=False)
            with inst, mock.patch.object(Prefix, "run") as run:
                self.assertFalse(wineasio.register(p)); run.assert_not_called()

    def test_no_prefix_does_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            p, inst = self._prefix(d)
            import shutil; shutil.rmtree(p.drive_c)
            with inst, mock.patch.object(Prefix, "run") as run:
                self.assertFalse(wineasio.register(p)); run.assert_not_called()

    def test_unregister_removes_copy_and_key_unless_running(self):
        with tempfile.TemporaryDirectory() as d:
            p, _ = self._prefix(d); dll = p.drive_c / "windows/system32/wineasio64.dll"; dll.write_bytes(b"x")
            with mock.patch.object(Prefix, "wineserver_running", return_value=True), mock.patch.object(Prefix, "run") as run:
                self.assertFalse(wineasio.unregister(p)); run.assert_not_called(); self.assertTrue(dll.exists())
            with mock.patch.object(Prefix, "wineserver_running", return_value=False), \
                 mock.patch.object(Prefix, "reg_query", return_value={"x": "1"}), mock.patch.object(Prefix, "run") as run:
                self.assertTrue(wineasio.unregister(p)); self.assertFalse(dll.exists())
                self.assertEqual(run.call_args.args[0], [Prefix.REG64, "delete", wineasio.KEY, "/f"])

    def test_copies_and_registers_once(self):
        with tempfile.TemporaryDirectory() as d:
            p, inst = self._prefix(d); state = {"reg": False}
            def run(args, **kw): state["reg"] = True; return subprocess.CompletedProcess(args, 0, "", "")
            with inst, mock.patch.object(Prefix, "run", side_effect=run) as r, \
                 mock.patch.object(Prefix, "reg_query", side_effect=lambda k: {"x": "1"} if state["reg"] else {}):
                self.assertTrue(wineasio.register(p))
                self.assertEqual((p.drive_c / "windows/system32/wineasio64.dll").read_bytes(), b"stub")
                self.assertEqual(r.call_args.args[0], ["regsvr32", "/s", "wineasio64.dll"])
                self.assertTrue(wineasio.register(p)); self.assertEqual(r.call_count, 1)   # second pass: nothing to do

    def test_setup_keeps_a_registration_current_but_never_makes_one(self):
        with tempfile.TemporaryDirectory() as d:
            p, inst = self._prefix(d)
            with inst, mock.patch.object(Prefix, "run") as run:
                self.assertFalse(wineasio.enabled(p)); self.assertFalse(wineasio.refresh(p)); run.assert_not_called()
            (p.drive_c / "windows/system32/wineasio64.dll").write_bytes(b"older stub")       # the user turned it on earlier
            with inst, mock.patch.object(Prefix, "run", return_value=subprocess.CompletedProcess([], 0, "", "")) as run, \
                 mock.patch.object(Prefix, "reg_query", return_value={"x": "1"}):
                self.assertTrue(wineasio.enabled(p)); self.assertTrue(wineasio.refresh(p))
                self.assertEqual((p.drive_c / "windows/system32/wineasio64.dll").read_bytes(), b"stub"); run.assert_called_once()

    def test_wine_gets_pipewires_jack_library_only_while_the_driver_is_on(self):
        jack = "/usr/lib/x86_64-linux-gnu/pipewire-0.3/jack"
        with tempfile.TemporaryDirectory() as d:
            p, _ = self._prefix(d)
            with mock.patch.object(wineasio, "_host_facts", return_value=(jack, "", "")):
                self.assertNotIn("LD_LIBRARY_PATH", p.wine_env())
                (p.drive_c / "windows/system32/wineasio64.dll").write_bytes(b"stub")
                self.assertEqual(p.wine_env()["LD_LIBRARY_PATH"], jack)
            with mock.patch.object(wineasio, "_host_facts", return_value=(jack, "/opt/x/lib", "")):
                self.assertEqual(p.wine_env()["LD_LIBRARY_PATH"], jack + ":/opt/x/lib")
            with mock.patch.object(wineasio, "_host_facts", return_value=("", "", "")):    # no PipeWire, or jackd runs
                self.assertNotIn("LD_LIBRARY_PATH", p.wine_env())
            with mock.patch.object(wineasio, "_host_facts", return_value=(jack, jack, "")):   # already first on the host
                self.assertNotIn("LD_LIBRARY_PATH", p.wine_env())

    def test_the_host_script_names_the_directory_and_the_hosts_own_paths(self):
        wineasio._host_facts.cache_clear()
        with mock.patch.object(wineasio.host, "sh", return_value="/usr/lib64/pipewire-0.3/jack\n/opt/lib\n/usr/bin:/bin\n"):
            self.assertEqual(wineasio.pipewire_jack_dir(), ("/usr/lib64/pipewire-0.3/jack", "/opt/lib"))
        wineasio._host_facts.cache_clear()
        with mock.patch.object(wineasio.host, "sh", return_value="\n\n/usr/bin:/bin\n"):     # no PipeWire, or jackd runs
            self.assertEqual(wineasio.pipewire_jack_dir(), ("", "")); self.assertEqual(wineasio._host_facts()[2], "/usr/bin:/bin")
        wineasio._host_facts.cache_clear()
        with mock.patch.object(wineasio.host, "sh", return_value=""):                         # the host could not be asked
            self.assertEqual(wineasio._host_facts(), ("", "", ""))
        wineasio._host_facts.cache_clear()

    def test_the_control_panel_launcher_is_on_wines_path_while_it_exists(self):
        with tempfile.TemporaryDirectory() as d:
            b = Path(d) / "wineasio-bin"
            with mock.patch.object(wineasio, "BIN", b), mock.patch.object(wineasio, "LAUNCHER", b / "wineasio-settings"), \
                 mock.patch.object(wineasio, "_host_facts", return_value=("", "", "/usr/bin:/bin")), \
                 mock.patch("vstenv.menu.launcher", return_value="flatpak run --command=vstenv io.github.dguedry.vstenv"):
                self.assertEqual(wineasio.launch_env(), {})                       # nothing written yet: PATH is left alone
                wineasio.write_launcher()
                text = (b / "wineasio-settings").read_text()
                self.assertTrue(text.startswith("#!/bin/sh\n")); self.assertIn("exec flatpak run --command=vstenv io.github.dguedry.vstenv wineasio settings\n", text)
                self.assertTrue((b / "wineasio-settings").stat().st_mode & 0o100)
                self.assertEqual(wineasio.launch_env(), {"PATH": f"{b}:/usr/bin:/bin"})
            with mock.patch.object(wineasio, "BIN", b), mock.patch.object(wineasio, "LAUNCHER", b / "wineasio-settings"), \
                 mock.patch.object(wineasio, "_host_facts", return_value=("", "", "")):
                self.assertEqual(wineasio.launch_env(), {}, "without the host's PATH ours must not replace it")

    def test_settings_default_to_the_drivers_and_are_checked_before_they_are_written(self):
        with tempfile.TemporaryDirectory() as d:
            p, _ = self._prefix(d)
            with mock.patch.object(Prefix, "reg_query", return_value={"Preferred buffersize": "256", "Number of inputs": "nonsense", "Number of outputs": "999"}):
                s = wineasio.settings(p)
            self.assertEqual((s["buffer"], s["inputs"], s["outputs"], s["fixed-buffer"], s["connect-hardware"], s["autostart-jack"]), (256, 16, 128, 1, 1, 0))
            with mock.patch.object(Prefix, "reg_add") as add:
                self.assertEqual(wineasio.set_setting(p, "buffer", "128"), 128)
                add.assert_called_once_with(wineasio.KEY_SETTINGS, "Preferred buffersize", "128", kind="REG_DWORD")
                for name, bad in (("buffer", 300), ("buffer", "x"), ("inputs", -1), ("fixed-buffer", 2), ("nope", 1)):
                    with self.assertRaises(ValueError): wineasio.set_setting(p, name, bad)
                self.assertEqual(add.call_count, 1)

if __name__ == "__main__": unittest.main()
