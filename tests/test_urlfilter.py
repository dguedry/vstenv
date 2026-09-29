"""The URL filter drops a vendor's storefront/content-scan URLs (IK plugins open
a browser tab per sound pack) and forwards everything else to winebrowser."""
import unittest
from vstenv import urlfilter


class UrlFilterBodyTest(unittest.TestCase):
    def test_body_drops_store_urls_and_opens_the_rest(self):
        body = urlfilter._cmd_body().lower()
        # every DROP pattern appears as a findstr guard
        for pat in urlfilter.DROP:
            self.assertIn(pat.lower(), body)
        # it forwards to the real winebrowser, and has a drop branch
        self.assertIn("winebrowser.exe", body)
        self.assertIn(":drop", body)
        self.assertIn(urlfilter.MARK, body)

    def test_drop_patterns_match_the_observed_ik_urls(self):
        # the two real SampleTank URLs seen spraying tabs
        seen_dropped = [
            "https://www.ikmultimedia.com/appsupd/st4sounds.php?a=st4sounds-londongrooves&k=9",
            "https://www.ikmultimedia.com/products/index.php/?A=st3sounds-londongrooves",
        ]
        for url in seen_dropped:
            self.assertTrue(any(pat.lower() in url.lower() for pat in urlfilter.DROP), url)

    def test_real_links_are_not_dropped(self):
        for url in ("https://www.ikmultimedia.com/userarea",
                    "https://accounts.google.com/signin",
                    "native-access://callback?token=abc",
                    "https://www.native-instruments.com/"):
            self.assertFalse(any(pat.lower() in url.lower() for pat in urlfilter.DROP), url)


class UrlFilterInstallTest(unittest.TestCase):
    def test_install_writes_wrapper_and_points_the_handlers(self):
        import tempfile
        from pathlib import Path
        from unittest import mock
        from vstenv.wine import Prefix, WineBuild
        with tempfile.TemporaryDirectory() as tmp:
            p = Prefix(Path(tmp) / "prefix", WineBuild(Path(tmp) / "wine"))
            (p.drive_c / "windows").mkdir(parents=True)
            reg = {k: {"(Default)": '"C:\\\\windows\\\\system32\\\\winebrowser.exe" "%1"'} for k in urlfilter._URL_KEYS}
            with mock.patch.object(p, "reg_query", side_effect=lambda k: reg.get(k, {})), \
                 mock.patch.object(p, "reg_set_default", side_effect=lambda k, v, kind="REG_SZ": reg.setdefault(k, {}).__setitem__("(Default)", v)):
                ok = urlfilter.install(p)
            self.assertTrue(ok)
            self.assertTrue(urlfilter.installed(p))
            for k in urlfilter._URL_KEYS:
                self.assertEqual(reg[k]["(Default)"], urlfilter.HANDLER)

    def test_install_leaves_a_foreign_handler_alone(self):
        import tempfile
        from pathlib import Path
        from unittest import mock
        from vstenv.wine import Prefix, WineBuild
        with tempfile.TemporaryDirectory() as tmp:
            p = Prefix(Path(tmp) / "prefix", WineBuild(Path(tmp) / "wine"))
            (p.drive_c / "windows").mkdir(parents=True)
            reg = {k: {"(Default)": '"C:\\\\Program Files\\\\Custom\\\\browser.exe" "%1"'} for k in urlfilter._URL_KEYS}
            with mock.patch.object(p, "reg_query", side_effect=lambda k: reg.get(k, {})), \
                 mock.patch.object(p, "reg_set_default", side_effect=lambda k, v, kind="REG_SZ": reg[k].__setitem__("(Default)", v)):
                urlfilter.install(p)
            for k in urlfilter._URL_KEYS:                    # a user's own browser handler is not clobbered
                self.assertIn("Custom", reg[k]["(Default)"])


if __name__ == "__main__": unittest.main()
