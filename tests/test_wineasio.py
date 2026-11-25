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

if __name__ == "__main__": unittest.main()
