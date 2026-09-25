"""The dependency-check patch: applied on NA <= 3.25's selector, recognised as
built-in on NA >= 3.26's, and reported as unsupported on anything else."""
import unittest
from vstenv.vendors.ni import native_access as na

# NA 3.25.2 (obfuscated): cand = owned[0] ?? players[0], then the owned filter
OLD = (b"_0x1a=_0x2b[0x0]??_0x3c[0x0],_0x4d=_0x2b[_0x9f(0x1234)](_0x5e=>_0x5e[_0x9f(0x77)]);"
       b"if(!_0x1a)return{'result':_0x9f(0x10)};")
# NA 3.26.0 (obfuscated): cand = bestInstalled ?? installed[0] ?? deploying ?? owned[0] ?? players[0]
NEW = (b"_0x547b78=_0x51eff3??_0x7dc46a[0x0]??_0x431d48??_0x382314[0x0]??_0x3cfd84[0x0];"
       b"if(!_0x547b78)return{'result':_0x2d60e5(0x2771)};")

class DependencyPatchTest(unittest.TestCase):
    def test_old_selector_is_patched(self):
        out = na._dep_edit(OLD)
        self.assertIsNotNone(out)
        self.assertIn(na.DEP_MARK.encode(), out)
        self.assertTrue(out.startswith(b"_0x1a=" + na.DEP_MARK.encode() + b"_0x2b.find(p=>p.isInstalled)??_0x3c.find(p=>p.isInstalled)??_0x2b[0x0]??_0x3c[0x0]"))
        self.assertIsNone(na._dep_edit(out))            # idempotent

    def test_new_selector_needs_no_patch(self):
        self.assertTrue(na.dependency_native(NEW))
        self.assertFalse(na.dependency_native(OLD))
        self.assertIsNone(na._dep_edit(NEW))

    def test_unknown_selector_is_unsupported(self):
        with self.assertRaises(LookupError):
            na._dep_edit(b"function nothingHere(){return 1}")
        with self.assertRaises(LookupError):        # two candidate sites: refuse rather than guess
            na._dep_edit(OLD + OLD)

if __name__ == "__main__":
    unittest.main()
