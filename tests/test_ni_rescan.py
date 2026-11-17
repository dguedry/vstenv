"""The NI rescan service goes into a prefix only when NI's hardware services are there."""
import tempfile, unittest
from pathlib import Path
from unittest import mock

from vstenv.vendors.ni import rescan
from vstenv.wine import Prefix, WineBuild


class Ensure(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); t = Path(self.tmp.name)
        self.p = Prefix(t / "prefix", WineBuild(t / "wine"))
        self.exe = t / "nirescan.exe"; self.exe.write_bytes(b"MZ new")
        self.reg = {}
        self.sc = []
        def reg_query(key): return self.reg.get(key.rsplit("\\", 1)[-1], {})
        def sc(*args):
            self.sc.append(args)
            if args[0] == "create": self.reg[args[1]] = {"ImagePath": args[3]}
            return ""
        self.patches = [mock.patch.object(self.p, "reg_query", side_effect=reg_query),
                        mock.patch.object(self.p, "sc", side_effect=sc),
                        mock.patch.object(self.p, "reg_add")]
        for m in self.patches: m.start()

    def tearDown(self):
        for m in self.patches: m.stop()
        self.tmp.cleanup()

    def test_nothing_without_ni_hardware_services(self):
        with mock.patch.object(rescan, "source", return_value=self.exe):
            self.assertFalse(rescan.ensure(self.p))
        self.assertEqual(self.sc, []); self.assertFalse(rescan.target(self.p).exists())

    def test_nothing_until_the_wine_fixes_carry_it(self):
        self.reg["NIHardwareService"] = {"ImagePath": "x"}
        with mock.patch.object(rescan, "source", return_value=None):
            self.assertFalse(rescan.ensure(self.p))
        self.assertEqual(self.sc, [])

    def test_installed_registered_and_started(self):
        self.reg["NIHostIntegrationAgent"] = {"ImagePath": "x"}
        with mock.patch.object(rescan, "source", return_value=self.exe):
            self.assertTrue(rescan.ensure(self.p))
        self.assertEqual(rescan.target(self.p).read_bytes(), b"MZ new")
        create = next(a for a in self.sc if a[0] == "create")
        self.assertEqual(create, ("create", "nirescan", "binPath=", r"C:\windows\system32\nirescan.exe", "start=", "auto"))
        self.assertEqual(self.sc[-1], ("start", "nirescan"))

    def test_a_newer_exe_replaces_the_old_one_with_the_service_stopped(self):
        self.reg["NIHardwareService"] = {"ImagePath": "x"}; self.reg["nirescan"] = {"ImagePath": "y"}
        t = rescan.target(self.p); t.parent.mkdir(parents=True); t.write_bytes(b"MZ old")
        with mock.patch.object(rescan, "source", return_value=self.exe):
            self.assertTrue(rescan.ensure(self.p))
        self.assertEqual(t.read_bytes(), b"MZ new")
        self.assertEqual(self.sc[0], ("stop", "nirescan"))
        self.assertFalse(any(a[0] == "create" for a in self.sc))


if __name__ == "__main__":
    unittest.main()
