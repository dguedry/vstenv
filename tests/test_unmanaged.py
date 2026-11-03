import unittest

from vstenv import unmanaged


class FakePrefix:
    """Just the registry surface unmanaged.py uses."""
    def __init__(self, values=None): self.values = dict(values or {})
    def reg_query(self, key): return dict(self.values.get(key, {}))
    def reg_add(self, key, name, value, kind="REG_SZ"):
        self.values.setdefault(key, {})[name] = value


class UnmanagedTest(unittest.TestCase):
    def test_key_is_per_program(self):
        self.assertEqual(unmanaged.key("Komplete Kontrol.exe"),
                         r"HKCU\Software\Wine\AppDefaults\Komplete Kontrol.exe\X11 Driver")

    def test_apply_sets_managed_n(self):
        p = FakePrefix()
        changed = unmanaged.apply(p, ["Komplete Kontrol.exe"])
        self.assertEqual(changed, ["Komplete Kontrol.exe"])
        self.assertTrue(unmanaged.applied(p, "Komplete Kontrol.exe"))
        self.assertEqual(p.values[unmanaged.key("Komplete Kontrol.exe")]["Managed"], "N")

    def test_apply_is_idempotent(self):
        p = FakePrefix()
        unmanaged.apply(p, ["Komplete Kontrol.exe"])
        self.assertEqual(unmanaged.apply(p, ["Komplete Kontrol.exe"]), [])

    def test_other_programs_are_untouched(self):
        p = FakePrefix()
        unmanaged.apply(p, ["Komplete Kontrol.exe"])
        self.assertFalse(unmanaged.applied(p, "Kontakt 8.exe"))

    def test_applied_is_false_for_other_values(self):
        p = FakePrefix({unmanaged.key("X.exe"): {"Managed": "Y"}})
        self.assertFalse(unmanaged.applied(p, "X.exe"))


if __name__ == "__main__":
    unittest.main()
