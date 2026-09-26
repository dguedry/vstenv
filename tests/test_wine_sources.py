import json, unittest
from unittest import mock
from vstenv import wine, download

class WineSourcesTest(unittest.TestCase):
    def test_own_release_first_then_upstream(self):
        rel = {"tag_name": "v0.1.9", "assets": [{"name": "yabridge-x.tar.gz", "browser_download_url": "u1"},
                                                 {"name": wine.WINE_BUILD["name"] + ".tar.xz", "browser_download_url": "https://github.com/dguedry/vstenv/releases/download/v0.1.9/w.tar.xz"}]}
        with mock.patch.object(download, "text", return_value=json.dumps(rel)):
            srcs = wine.wine_tarball_sources()
        self.assertEqual([u for u, _ in srcs], ["https://github.com/dguedry/vstenv/releases/download/v0.1.9/w.tar.xz", wine.WINE_BUILD["url"]])
        self.assertIn("release v0.1.9", srcs[0][1])
    def test_upstream_only_when_the_release_has_no_wine_or_is_unreachable(self):
        with mock.patch.object(download, "text", return_value=json.dumps({"assets": []})):
            self.assertEqual(wine.wine_tarball_sources(), [(wine.WINE_BUILD["url"], "upstream build")])
        with mock.patch.object(download, "text", side_effect=OSError("offline")):
            self.assertEqual(wine.wine_tarball_sources(), [(wine.WINE_BUILD["url"], "upstream build")])

if __name__ == "__main__": unittest.main()
