import unittest
from pathlib import Path
from unittest import mock

from vstenv import vendors


class PendingManagerUpdateTest(unittest.TestCase):
    """An update Native Access downloaded but did not install (its updater
    failing, or an install from when this app switched the updater off) can be
    installed through this app."""

    def test_every_vendor_answers_the_hook(self):
        """Vendors with no self-updating manager answer None without touching
        the prefix; NI implements it for real and is covered below."""
        p = mock.Mock()
        for v in vendors.all():
            if v.id == "ni": continue
            self.assertIsNone(v.pending_manager_update(p), v.id)

    def test_a_vendor_without_a_manager_applies_nothing(self):
        am = vendors.get("am")
        self.assertFalse(am.apply_pending_manager_update(mock.Mock()))

    def test_ni_reports_the_file_native_access_left(self):
        ni = vendors.get("ni")
        from vstenv.vendors.ni import native_access as na
        with mock.patch.object(na, "pending_update", return_value=Path("/x/Native-Access-latest.exe")):
            self.assertEqual(ni.pending_manager_update(mock.Mock()),
                             Path("/x/Native-Access-latest.exe"))

    def test_ni_applies_it_through_this_app(self):
        """It must go through install_native_access, which re-applies the fixes
        -- installing it any other way would leave NA unpatched."""
        ni = vendors.get("ni")
        from vstenv.vendors.ni import native_access as na
        with mock.patch.object(na, "apply_pending_update", return_value=True) as applied:
            self.assertTrue(ni.apply_pending_manager_update(mock.Mock()))
        applied.assert_called_once()

    def test_nothing_pending_is_not_an_error(self):
        ni = vendors.get("ni")
        from vstenv.vendors.ni import native_access as na
        with mock.patch.object(na, "pending_update", return_value=None):
            self.assertIsNone(ni.pending_manager_update(mock.Mock()))


class NativeAccessStaysStockTest(unittest.TestCase):
    """The design since 2026-11: NA runs on NI's own files and updates itself.
    Nothing this app does on install, repair or launch may edit the bundle to
    switch updates off or build switches in; old installs get NI's files back."""

    def _tree(self, tmp, asar_bytes, backups=True, pair=True):
        import hashlib, struct
        from vstenv.vendors.ni import native_access as na
        d = Path(tmp) / "drive_c" / na.NA_REL; (d / "resources").mkdir(parents=True)
        def asar(payload):
            hj = b'{"files":{}}'
            return struct.pack("<IIII", 4, len(hj) + 8, len(hj) + 4, len(hj)) + hj + payload, hashlib.sha256(hj).hexdigest()
        stock, h = asar(b"autoUpdateEnabled:!0")
        (d / "resources/app.asar").write_bytes(asar(asar_bytes)[0])
        (d / "Native Access.exe").write_bytes(b"MZ edited")
        if backups:
            (d / "resources/app.asar.orig").write_bytes(stock)
            (d / "Native Access.exe.pre-asarpatch").write_bytes(b"MZ stock " + (h.encode() if pair else b"other"))
        p = mock.Mock(); p.drive_c = Path(tmp) / "drive_c"; p.is_running.return_value = False
        return p, d, stock

    def test_an_edited_install_gets_nis_files_back(self):
        import tempfile
        from vstenv.vendors.ni import native_access as na
        with tempfile.TemporaryDirectory() as tmp:
            p, d, stock = self._tree(tmp, b"autoUpdateEnabled:!1" + na._FLAGS_MARK)
            self.assertEqual(na.stock_status(p), "edited")
            self.assertEqual(na.restore_stock(p), "restored")
            self.assertEqual((d / "resources/app.asar").read_bytes(), stock)
            self.assertTrue((d / "Native Access.exe").read_bytes().startswith(b"MZ stock"))
            self.assertEqual(na.stock_status(p), "stock")
            self.assertFalse((d / "resources/app.asar.orig").exists())      # stale copies would lie after NA's next update
            self.assertEqual(na.restore_stock(p), "stock")                  # idempotent

    def test_it_never_touches_a_running_or_unpaired_install(self):
        import tempfile
        from vstenv.vendors.ni import native_access as na
        with tempfile.TemporaryDirectory() as tmp:
            p, d, _ = self._tree(tmp, b"autoUpdateEnabled:!1")
            p.is_running.return_value = True
            self.assertEqual(na.restore_stock(p), "running")
            self.assertEqual((d / "Native Access.exe").read_bytes(), b"MZ edited")
        with tempfile.TemporaryDirectory() as tmp:
            p, d, _ = self._tree(tmp, b"autoUpdateEnabled:!1", pair=False)   # exe copy from another NA version
            self.assertEqual(na.restore_stock(p), "unsupported")
            self.assertEqual((d / "Native Access.exe").read_bytes(), b"MZ edited")
        with tempfile.TemporaryDirectory() as tmp:
            p, d, _ = self._tree(tmp, b"autoUpdateEnabled:!1", backups=False)
            self.assertEqual(na.restore_stock(p), "unsupported")

    def test_install_repair_and_launch_leave_the_bundle_alone(self):
        from vstenv.vendors.ni import native_access as na
        import inspect
        for fn in (na.install_native_access, na.install_native_access_fixes, na.launch):
            src = inspect.getsource(fn)
            for gone in ("disable_self_update", "bake_launch_flags", "--disable-gpu", "--no-sandbox"):
                self.assertNotIn(gone, src, fn.__name__)
        self.assertIn("restore_stock", inspect.getsource(na.install_native_access_fixes))
        self.assertIn("composition_off", inspect.getsource(na.launch))

    def test_composition_is_switched_off_for_native_access_only(self):
        from vstenv.vendors.ni import native_access as na
        p = mock.Mock(); p.reg_query.return_value = {}
        self.assertTrue(na.composition_off(p))
        p.reg_add.assert_called_once_with(r"HKCU\Software\Wine\AppDefaults\Native Access.exe\DllOverrides", "dcomp", "disabled")
        p.reg_query.return_value = {"dcomp": "disabled"}; p.reg_add.reset_mock()
        self.assertFalse(na.composition_off(p)); p.reg_add.assert_not_called()

    def test_a_newer_bundled_daemon_is_installed(self):
        import tempfile
        from vstenv.vendors.ni import native_access as na
        with tempfile.TemporaryDirectory() as tmp:
            p = mock.Mock(); p.drive_c = Path(tmp)
            dd = Path(tmp) / na.NA_REL / "resources/daemon/win"; dd.mkdir(parents=True)
            (dd / "NTKDaemon 1.33.0 Setup PC.exe").write_bytes(b"x")
            self.assertEqual(na.bundled_daemon_version(p), "1.33.0")
            (Path(tmp) / na.NTK_REL).mkdir(parents=True); (Path(tmp) / na.NTK_REL / "NTKDaemon.exe").write_bytes(b"x")
            with mock.patch.object(na, "ntk_daemon") as inst, mock.patch.object(na.pe, "version", return_value="1.32.0.5"):
                self.assertTrue(na.daemon_current(p)); inst.assert_called_once()
            with mock.patch.object(na, "ntk_daemon") as inst, mock.patch.object(na.pe, "version", return_value="1.33.0.1"):
                self.assertFalse(na.daemon_current(p)); inst.assert_not_called()


if __name__ == "__main__":
    unittest.main()
