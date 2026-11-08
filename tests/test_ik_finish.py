import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from vstenv.vendors.ik import IKMultimedia


class FinishSinceTest(unittest.TestCase):
    """Closing the Product Manager finishes only what was downloaded in that session."""
    def test_old_download_is_left_alone(self):
        with tempfile.TemporaryDirectory() as d:
            p = SimpleNamespace(user_dir=Path(d))
            root = Path(d) / "Documents/IK Multimedia/IK Product Manager"
            old = root / "SampleTank 4"; new = root / "TONEX"
            for sub in (old, new):
                sub.mkdir(parents=True); (sub / "setup.exe").write_bytes(b"MZ")
            since = time.time()
            os.utime(old / "setup.exe", (since - 86400, since - 86400))
            ik = IKMultimedia(); ran = []
            with mock.patch.object(ik, "_newest", side_effect=lambda s: since - 86400 if s == old else since + 1), \
                 mock.patch("vstenv.programs.installed", return_value=[]), \
                 mock.patch("vstenv.programs.install", side_effect=lambda p, exe, r: ran.append(exe.parent.name) or 0):
                ik.finish_installs(p, None, since=since)
                self.assertEqual(ran, ["TONEX"])
                ran.clear(); ik.finish_installs(p, None)          # the manual "Finish interrupted installs": all of them
                self.assertEqual(ran, ["SampleTank 4"])          # TONEX was marked finished by the first run


if __name__ == "__main__": unittest.main()
