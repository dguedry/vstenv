import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from vstenv import urlschemes

REG = r'''WINE REGISTRY Version 2

[Software\\Classes\\kchub] 1794447550
@="URL:kchub"
"URL Protocol"=""

[Software\\Classes\\kchub\\shell\\open\\command] 1794447550
@="\"C:\\Program Files\\KORG\\KORG Software Pass.exe\" \"%1\""

[Software\\Classes\\sam] 1
"URL Protocol"=""

[Software\\Classes\\sam\\shell\\open\\command] 1
@="\"C:\\Program Files\\Sam\\Sam.exe\" --redirect \"%1\""

[Software\\Classes\\http] 1
"URL Protocol"=""

[Software\\Classes\\http\\shell\\open\\command] 1
@="\"C:\\windows\\system32\\winebrowser.exe\" -nohome \"%1\""

[Software\\Classes\\ownbrowser] 1
"URL Protocol"=""

[Software\\Classes\\ownbrowser\\shell\\open\\command] 1
@="\"C:\\windows\\system32\\winebrowser.exe\" \"%1\""

[Software\\Classes\\gone] 1
"URL Protocol"=""

[Software\\Classes\\gone\\shell\\open\\command] 1
@="\"C:\\Program Files\\Gone\\Gone.exe\" \"%1\""

[Software\\Classes\\middle] 1
"URL Protocol"=""

[Software\\Classes\\middle\\shell\\open\\command] 1
@="\"C:\\Program Files\\Sam\\Sam.exe\" \"%1\" --then"

[Software\\Classes\\.txt] 1
@="txtfile"
'''


class DiscoveredSchemesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); d = Path(self.tmp.name)
        (d / "system.reg").write_text(REG)
        for exe in ("Program Files/KORG/KORG Software Pass.exe", "Program Files/Sam/Sam.exe"):
            f = d / "drive_c" / exe; f.parent.mkdir(parents=True, exist_ok=True); f.write_bytes(b"MZ")
        def to_host(w):
            return d / "drive_c" / w[3:].replace("\\", "/")
        self.p = SimpleNamespace(path=d, drive_c=d / "drive_c", to_host=to_host,
                                 build=SimpleNamespace(wine=Path("/w/bin/wine")))
    def tearDown(self): self.tmp.cleanup()

    def test_finds_program_schemes_only(self):
        got = {s.scheme: s for s in urlschemes.program_schemes(self.p)}
        self.assertEqual(sorted(got), ["kchub", "sam"])     # not Wine's, not a missing exe, not %1 mid-line
        self.assertEqual(got["kchub"].argv(self.p),
                         ["/w/bin/wine", str(self.p.drive_c / "Program Files/KORG/KORG Software Pass.exe")])
        self.assertEqual(got["sam"].argv(self.p)[-1], "--redirect")
        self.assertEqual(got["kchub"].title, "KORG Software Pass link handler")

    def test_a_vendor_declared_scheme_wins(self):
        self.assertEqual([s.scheme for s in urlschemes.program_schemes(self.p, {"kchub"})], ["sam"])

    def test_split_command(self):
        self.assertEqual(urlschemes._split_command(r'"C:\A B\x.exe" --r "%1"'), [r"C:\A B\x.exe", "--r", "%1"])


if __name__ == "__main__": unittest.main()
