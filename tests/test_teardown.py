import tempfile
import unittest
from pathlib import Path
from unittest import mock

from vstenv import teardown


class TeardownTest(unittest.TestCase):
    def test_deletes_data_but_never_follows_the_prefixs_links(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d); data = d / "data"; cfg = d / "config"; cache = d / "cache"; home_docs = d / "Documents"
            home_docs.mkdir(); (home_docs / "keep.txt").write_text("mine")
            dos = data / "prefix/dosdevices"; dos.mkdir(parents=True); (dos / "z:").symlink_to("/")
            user = data / "prefix/drive_c/users/me"; user.mkdir(parents=True); (user / "Documents").symlink_to(home_docs)
            cfg.mkdir(); cache.mkdir(); (cache / "wine.tar.xz").write_text("x")
            with mock.patch.object(teardown.paths, "DATA", data), mock.patch.object(teardown.paths, "CONFIG", cfg), \
                 mock.patch.object(teardown.paths, "CACHE", cache), \
                 mock.patch("vstenv.menu.remove_all"), mock.patch("vstenv.filemanager.remove"), mock.patch("vstenv.desktopfix.reset"):
                teardown.remove_environment(None)
            self.assertFalse(data.exists()); self.assertFalse(cfg.exists())
            self.assertTrue((cache / "wine.tar.xz").exists())          # downloads stay unless asked
            self.assertEqual((home_docs / "keep.txt").read_text(), "mine")
            self.assertTrue(Path("/etc").exists())


if __name__ == "__main__": unittest.main()
