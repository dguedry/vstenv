import tempfile
import types
import unittest
from pathlib import Path

from vstenv import displaycache

STALE = "[System\\\\ControlSet001\\\\Enum\\\\DISPLAY\\\\Default_Monitor\\\\0000&0000] 1792164665\n"
REAL = "[System\\\\ControlSet001\\\\Enum\\\\DISPLAY\\\\ACR06E3\\\\0001&0000] 1792164665\n"


class DisplayCacheTest(unittest.TestCase):
    """2026-10-16: a 640x480 virtual-desktop probe left 'Default_Monitor' in the
    shared display cache; every window on a 1920x1080 host was then clamped
    to 640x480 and Native Access painted a corner of its window."""

    def _prefix(self, reg):
        d = tempfile.TemporaryDirectory(); self.addCleanup(d.cleanup)
        if reg is not None: (Path(d.name) / "system.reg").write_text(reg)
        return types.SimpleNamespace(path=Path(d.name))

    def test_virtual_monitor_is_stale(self):
        self.assertTrue(displaycache.stale_virtual_monitor(self._prefix(REAL + STALE)))

    def test_real_monitors_only_is_fine(self):
        self.assertFalse(displaycache.stale_virtual_monitor(self._prefix(REAL)))

    def test_missing_registry_is_not_a_finding(self):
        self.assertFalse(displaycache.stale_virtual_monitor(self._prefix(None)))

    def test_doctor_has_the_check(self):
        src = Path("vstenv/doctor.py").read_text()
        self.assertIn("stale_virtual_monitor", src)
