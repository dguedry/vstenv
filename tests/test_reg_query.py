import unittest
from vstenv.wine import _parse_reg_export


class RegExportParseTest(unittest.TestCase):
    """reg_query reads `reg export` (UTF-16) rather than `reg query` output,
    whose localized text comes back in the prefix's OEM code page. Reported by
    a German user: a missing key aborted the Native Access install with a
    UnicodeDecodeError on the 'not found' message (cp850 0x81 for 'ü')."""

    def test_string_and_dword(self):
        vals = _parse_reg_export(
            '\ufeffWindows Registry Editor Version 5.00\r\n\r\n'
            '[HKEY_LOCAL_MACHINE\\System\\CurrentControlSet\\Services\\NTKDaemon]\r\n'
            '"ImagePath"="C:\\\\Program Files\\\\NTKDaemon.exe"\r\n'
            '"Start"=dword:00000002\r\n')
        self.assertEqual(vals["ImagePath"], r"C:\Program Files\NTKDaemon.exe")
        self.assertEqual(vals["Start"], "2")

    def test_non_ascii_value_survives(self):
        """The whole point: a path with an umlaut round-trips intact, where
        decoding the OEM-code-page pipe would have garbled it."""
        vals = _parse_reg_export(
            '\ufeffWindows Registry Editor Version 5.00\r\n\r\n'
            '[HKEY_CURRENT_USER\\Software\\NI]\r\n'
            '"DownloadLocation"="C:\\\\users\\\\me\\\\Musik\\\\Schl\u00fcssel"\r\n')
        self.assertEqual(vals["DownloadLocation"], "C:\\users\\me\\Musik\\Schl\u00fcssel")

    def test_expand_sz_hex(self):
        vals = _parse_reg_export(
            '[HKEY_LOCAL_MACHINE\\Software\\X]\r\n'
            '"Path"=hex(2):43,00,3a,00,5c,00,54,00,65,00,6d,00,70,00,00,00\r\n')
        self.assertEqual(vals["Path"], "C:\\Temp")

    def test_line_continuation(self):
        vals = _parse_reg_export(
            '[HKEY_LOCAL_MACHINE\\Software\\X]\r\n'
            '"Hex"=hex(2):43,00,3a,00,\\\r\n'
            '  5c,00,54,00,65,00,6d,00,70,00,00,00\r\n')
        self.assertEqual(vals["Hex"], "C:\\Temp")

    def test_missing_key_is_empty(self):
        self.assertEqual(_parse_reg_export(""), {})
        self.assertEqual(_parse_reg_export("\ufeffWindows Registry Editor Version 5.00\r\n"), {})

    def test_quotes_are_part_of_the_value(self):
        """A service ImagePath is stored with its quotes; `reg query` printed
        them too, so callers that already compare this keep working."""
        vals = _parse_reg_export('[K]\r\n"ImagePath"="\\"C:\\\\x.exe\\""\r\n')
        self.assertEqual(vals["ImagePath"], '"C:\\x.exe"')


if __name__ == "__main__":
    unittest.main()
