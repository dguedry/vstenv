import unittest

from vstenv import unmanaged


class FakePrefix:
    """Just the registry surface unmanaged.py uses."""
    def __init__(self, values=None): self.values = dict(values or {})
    def reg_query(self, key): return dict(self.values.get(key, {}))
    def reg_add(self, key, name, value, kind="REG_SZ"):
        self.values.setdefault(key, {})[name] = value
    def reg_delete_value(self, key, name):
        self.values.get(key, {}).pop(name, None)


class FakeProgram:
    def __init__(self, name, exe): self.name, self.exe = name, exe


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


class PerProgramTest(unittest.TestCase):
    """The switch the GUI and `vstenv unmanaged` drive."""
    def setUp(self):
        self.p = FakePrefix()
        self.prog = FakeProgram("Komplete Kontrol", r"C:\Program Files\NI\Komplete Kontrol.exe")

    def test_exe_name_is_the_basename(self):
        self.assertEqual(unmanaged.exe_name(self.prog), "Komplete Kontrol.exe")

    def test_round_trip_on_then_off(self):
        self.assertTrue(unmanaged.set_for(self.p, self.prog, True))
        self.assertTrue(unmanaged.is_on(self.p, self.prog))
        self.assertTrue(unmanaged.set_for(self.p, self.prog, False))
        self.assertFalse(unmanaged.is_on(self.p, self.prog))

    def test_setting_the_same_state_twice_changes_nothing(self):
        unmanaged.set_for(self.p, self.prog, True)
        self.assertFalse(unmanaged.set_for(self.p, self.prog, True))
        unmanaged.set_for(self.p, self.prog, False)
        self.assertFalse(unmanaged.set_for(self.p, self.prog, False))

    def test_a_program_without_a_launcher_is_not_offered(self):
        p2 = FakeProgram("Uninstall-only thing", None)
        self.assertFalse(unmanaged.supported(p2))
        self.assertFalse(unmanaged.set_for(self.p, p2, True))
        self.assertFalse(unmanaged.is_on(self.p, p2))

    def test_supported_for_a_program_with_a_launcher(self):
        self.assertTrue(unmanaged.supported(self.prog))


if __name__ == "__main__":
    unittest.main()
