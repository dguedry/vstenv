"""runtimedir: a /run/user/<uid>/wine open to others is made private, and only if it is ours."""
import os, subprocess, tempfile, unittest
from pathlib import Path
from unittest import mock
from vstenv import runtimedir

def _local_sh(script, env=None, timeout=60):
    return subprocess.run(["sh", "-c", script], capture_output=True, text=True).stdout

class RuntimeDirTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.run = Path(self.tmp.name)
        self.env = mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(self.run)})
        self.sh = mock.patch.object(runtimedir.host, "sh", side_effect=_local_sh)
        self.env.start(); self.sh.start()
    def tearDown(self):
        self.sh.stop(); self.env.stop(); self.tmp.cleanup()

    def test_absent_folder_is_fine(self):
        self.assertTrue(runtimedir.status()["ok"]); self.assertFalse(runtimedir.heal())
    def test_open_folder_is_made_private(self):
        d = self.run / "wine"; d.mkdir(); d.chmod(0o755)
        s = runtimedir.status(); self.assertFalse(s["ok"]); self.assertEqual(s["mode"], "755"); self.assertEqual(s["path"], str(d))
        self.assertTrue(runtimedir.heal())
        self.assertEqual(d.stat().st_mode & 0o777, 0o700); self.assertTrue(runtimedir.status()["ok"])
    def test_private_folder_is_left_alone(self):
        d = self.run / "wine"; d.mkdir(); d.chmod(0o700)
        self.assertTrue(runtimedir.status()["ok"]); self.assertFalse(runtimedir.heal())
    def test_unreachable_host_says_nothing(self):
        with mock.patch.object(runtimedir.host, "sh", return_value=""):
            self.assertTrue(runtimedir.status()["ok"])

if __name__ == "__main__": unittest.main()
