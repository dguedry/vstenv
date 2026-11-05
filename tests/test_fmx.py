"""FireMonkey programs get d3d11 and dxgi disabled for their exe, so FMX takes
its GDI+ canvas instead of a Direct3D one that draws wrongly under Wine."""
import tempfile, unittest
from pathlib import Path
from unittest import mock

from vstenv import fmx
from vstenv.wine import Prefix, WineBuild


def _prefix(tmp):
    p = Prefix(Path(tmp) / "prefix", WineBuild(Path(tmp) / "wine"))
    (p.drive_c / "users/me").mkdir(parents=True, exist_ok=True)
    return p


class DetectTest(unittest.TestCase):
    def test_both_markers_make_a_firemonkey_program(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "Jamstix4Manager.exe"
            f.write_bytes(b"MZ" + b"\0" * 64 + b"FMX.Platform.Win\0FMX.Canvas.GDIP\0")
            self.assertTrue(fmx.is_fmx(f))

    def test_one_marker_or_a_plain_program_is_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "a.exe"; f.write_bytes(b"MZ" + b"\0" * 64 + b"FMX.Platform.Win\0")
            self.assertFalse(fmx.is_fmx(f))
            g = Path(tmp) / "b.exe"; g.write_bytes(b"MZ" + b"\0" * 64 + b"Vcl.Forms\0")
            self.assertFalse(fmx.is_fmx(g))
            self.assertFalse(fmx.is_fmx(Path(tmp) / "missing.exe"))


class ApplyTest(unittest.TestCase):
    def test_apply_writes_both_overrides_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = _prefix(tmp); writes = []; store = {}
            with mock.patch.object(Prefix, "reg_add", lambda self, k, n, v, kind="REG_SZ": writes.append((k, n, v)) or store.__setitem__(n, v)), \
                 mock.patch.object(Prefix, "reg_query", lambda self, k: dict(store)):
                self.assertTrue(fmx.apply(p, "Jamstix4Manager.exe"))
                self.assertFalse(fmx.apply(p, "Jamstix4Manager.exe"))      # already in place
            self.assertEqual(sorted(n for _, n, _ in writes), ["d3d11", "dxgi"])
            self.assertTrue(all(v == "disabled" for _, _, v in writes))
            self.assertTrue(all(k == fmx.key("Jamstix4Manager.exe") for k, _, _ in writes))
            self.assertNotIn("d3d9", [n for _, n, _ in writes])             # it may import d3d9 directly

    def test_apply_if_fmx_leaves_other_programs_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = _prefix(tmp)
            f = Path(tmp) / "Setup.exe"; f.write_bytes(b"MZ" + b"\0" * 64 + b"Inno Setup")
            with mock.patch.object(Prefix, "reg_add") as add:
                self.assertFalse(fmx.apply_if_fmx(p, f))
            add.assert_not_called()


class HookTest(unittest.TestCase):
    """programs.install runs an installer exe through the override, and
    programs.run an installed program, before either starts."""
    def test_install_and_run_consult_fmx(self):
        src = Path("vstenv/programs.py").read_text()
        i = src.index("def install(p: Prefix, installer"); body = src[i:src.index("p.run(argv", i)]
        self.assertIn("fmx.apply_if_fmx(p, installer", body)
        j = src.index("def run(p: Prefix, prog"); body = src[j:src.index("proc = p.spawn(", j)]
        self.assertIn("fmx.apply_if_fmx(p, p.to_host(prog.exe)", body)


if __name__ == "__main__":
    unittest.main()
