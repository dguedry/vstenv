import unittest
from pathlib import Path
from unittest import mock
from vstenv import wine, host

LDD = """== capi2032.so
\tlibcapi20.so.3 => not found
== ntdll.so
== winedmo.so
\tntdll.so => not found
\tlibavutil.so.56 => not found
\tlibavformat.so.58 => not found
\tlibavcodec.so.58 => not found
== winex11.so
\tntdll.so => not found
\twin32u.so => not found
"""

class WineModuleLibsTest(unittest.TestCase):
    def test_missing_libs_are_grouped_per_module_ignoring_wines_own(self):
        with mock.patch.object(host, "sh", return_value=LDD):
            m = wine.missing_module_libs(wine.WineBuild(Path("/nonexistent")))
        self.assertEqual(m, {"capi2032": ["libcapi20.so.3"], "winedmo": ["libavutil.so.56", "libavformat.so.58", "libavcodec.so.58"]})
    def test_optional_losses_are_fine_essential_ones_are_not(self):
        ok, detail = wine.module_libs_check({"winedmo": ["libavcodec.so.58"]})
        self.assertTrue(ok); self.assertIn("winedmo", detail)
        self.assertEqual(wine.module_libs_check({}), (True, "every module's host libraries are present"))
        ok, detail = wine.module_libs_check({"winex11": ["libX11.so.6"]})
        self.assertFalse(ok); self.assertIn("winex11 needs libX11.so.6", detail)
        self.assertTrue(wine.module_libs_check({"winepulse": ["libpulse.so.0"]})[0], "ALSA still gives audio")
        self.assertFalse(wine.module_libs_check({"winepulse": ["libpulse.so.0"], "winealsa": ["libasound.so.2"]})[0])

if __name__ == "__main__": unittest.main()
