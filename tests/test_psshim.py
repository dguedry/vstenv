import unittest
from pathlib import Path

from vstenv import winefixes


class PowerShellShimTest(unittest.TestCase):
    """Wine's powershell.exe stub exits 0 for anything, so electron-builder
    installers were told their app was always running (Softube Central,
    2026-10-30). Disabling PowerShell for them broke the Softube Helper's
    service scripts, which rely on the stub's "success". The shim answers the
    process question honestly and keeps the stub's answer for the rest."""

    SRC = Path("tools/psshim/powershell.c")

    def test_shipped_in_both_bitnesses(self):
        self.assertIn("powershell.exe", winefixes.FIXES)
        self.assertIn("i386-windows/powershell.exe", winefixes.FIXES)

    def test_folders_in_names_land_under_lib_wine(self):
        import types
        b = types.SimpleNamespace(root=Path("/w"))
        self.assertEqual(winefixes.dest(b, "dcomp.dll"), Path("/w/lib/wine/x86_64-windows/dcomp.dll"))
        self.assertEqual(winefixes.dest(b, "i386-windows/powershell.exe"), Path("/w/lib/wine/i386-windows/powershell.exe"))

    def test_source_answers_the_process_question_and_stubs_the_rest(self):
        src = self.SRC.read_text()
        for needle in ("Win32_Process", "StartsWith(", "QueryFullProcessImageNameW", "Stop-Process", "log_line(L\"stub\""):
            self.assertIn(needle, src)

    def test_build_script_tags_it_as_a_wine_builtin(self):
        sh = Path("scripts/build-wine-fixes.sh").read_text()
        self.assertIn("tools/psshim/powershell.c", sh)
        self.assertIn("Wine builtin DLL", sh)
        self.assertIn("i686-w64-mingw32-gcc", sh)

    def test_nothing_disables_powershell_any_more(self):
        for f in ("vstenv/programs.py", "vstenv/quirks.py"):
            self.assertNotIn("powershell.exe=d", Path(f).read_text())
