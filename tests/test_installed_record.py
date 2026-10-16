import json
import tempfile
import types
import unittest
from pathlib import Path

from vstenv.installers import installaware as ia


class InstalledRecordTest(unittest.TestCase):
    """A deployed NI product without its installed_products record is "not
    installed" to Native Access, Komplete Kontrol and this app (Absynth 5,
    2026-10-16)."""

    def _prefix(self):
        d = tempfile.TemporaryDirectory(); self.addCleanup(d.cleanup)
        return types.SimpleNamespace(public_docs=Path(d.name))

    def test_record_is_the_registry_values_as_json(self):
        p = self._prefix()
        k = "SOFTWARE\\Native Instruments\\Absynth 5\\"
        out = ia.write_installed_record(p, {k + "InstallDir": "C:\\Program Files\\Native Instruments\\Absynth 5\\",
                                           k + "ContentDir": "C:\\Program Files\\Common Files\\Native Instruments\\Absynth 5\\",
                                           k + "ContentVersion": "5.3.4"})
        self.assertEqual(out, p.public_docs / "Native Instruments/installed_products/Absynth 5.json")
        j = json.loads(out.read_text())
        self.assertEqual(j["ContentVersion"], "5.3.4")
        self.assertEqual(j["InstallDir"], "C:\\Program Files\\Native Instruments\\Absynth 5", "NI writes dirs without the trailing backslash")
        self.assertEqual(j["ContentDir"], "C:\\Program Files\\Common Files\\Native Instruments\\Absynth 5")

    def test_nothing_written_without_a_product_key(self):
        p = self._prefix()
        self.assertIsNone(ia.write_installed_record(p, {"SOFTWARE\\Other\\X\\Value": "1"}))
        self.assertFalse((p.public_docs / "Native Instruments").exists())

    def test_deploy_writes_it(self):
        src = Path("vstenv/installers/installaware.py").read_text()
        start = src.index("def _deploy_and_register"); end = src.index("def write_installed_record")
        self.assertIn("write_installed_record(p, regkeys, r)", src[start:end])
