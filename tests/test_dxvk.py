"""DXVK is installed only when the machine has a hardware Vulkan driver, and the
prefix records what was installed so it can be undone."""
import json, tarfile, tempfile, unittest
from pathlib import Path
from unittest import mock

from vstenv import dxvk
from vstenv.wine import Prefix, WineBuild

VULKANINFO = """Devices:
========
GPU0:
\tapiVersion         = 1.4.312
\tdeviceType         = PHYSICAL_DEVICE_TYPE_DISCRETE_GPU
\tdeviceName         = NVIDIA GeForce RTX 4060
\tdriverName         = NVIDIA
GPU1:
\tapiVersion         = 1.4.318
\tdeviceType         = PHYSICAL_DEVICE_TYPE_CPU
\tdeviceName         = llvmpipe (LLVM 20.1.2, 256 bits)
\tdriverName         = llvmpipe
"""
AMD_ONLY = """Devices:
========
GPU0:
\tdeviceType         = PHYSICAL_DEVICE_TYPE_INTEGRATED_GPU
\tdeviceName         = AMD Radeon Graphics (RADV RENOIR)
\tdriverName         = radv
"""
SOFTWARE_ONLY = """Devices:
========
GPU0:
\tdeviceType         = PHYSICAL_DEVICE_TYPE_CPU
\tdeviceName         = llvmpipe (LLVM 20.1.2, 256 bits)
\tdriverName         = llvmpipe
"""

def _vulkaninfo(stdout):
    """Patch shutil.which + subprocess.run so vulkan_devices() sees `stdout`."""
    return (mock.patch.object(dxvk.shutil, "which", return_value="/usr/bin/vulkaninfo"),
            mock.patch.object(dxvk.subprocess, "run", return_value=mock.Mock(stdout=stdout, returncode=0)))

class VulkanDetectionTest(unittest.TestCase):
    def test_hardware_gpu_alongside_software_is_usable(self):
        w, s = _vulkaninfo(VULKANINFO)
        with w, s:
            devs = dxvk.vulkan_devices()
            self.assertEqual([d["name"] for d in devs], ["NVIDIA GeForce RTX 4060", "llvmpipe (LLVM 20.1.2, 256 bits)"])
            self.assertEqual([d["software"] for d in devs], [False, True])
            ok, detail = dxvk.vulkan_ok()
            self.assertTrue(ok); self.assertIn("RTX 4060", detail); self.assertNotIn("llvmpipe", detail)

    def test_integrated_amd_is_usable(self):
        w, s = _vulkaninfo(AMD_ONLY)
        with w, s:
            ok, detail = dxvk.vulkan_ok()
            self.assertTrue(ok); self.assertIn("RADV", detail)

    def test_software_only_is_not_usable(self):
        w, s = _vulkaninfo(SOFTWARE_ONLY)
        with w, s:
            ok, detail = dxvk.vulkan_ok()
            self.assertFalse(ok); self.assertIn("software rendering", detail)

    def test_icd_manifests_are_not_evidence_of_hardware(self):
        """Regression: Mesa ships a manifest per GPU family on every machine, so
        counting them said a GPU-less VM had twelve devices. Only devices the
        Vulkan loader actually enumerates count."""
        with mock.patch.object(dxvk.shutil, "which", return_value=None), \
             mock.patch.object(dxvk, "_devices_via_loader", return_value=[]):
            self.assertEqual(dxvk.vulkan_devices(), [])
            ok, _ = dxvk.vulkan_ok()
            self.assertFalse(ok)

    def test_loader_reporting_only_llvmpipe_is_not_usable(self):
        """A VM with no GPU: the loader enumerates llvmpipe (device type 4)."""
        with mock.patch.object(dxvk.shutil, "which", return_value=None), \
             mock.patch.object(dxvk, "_devices_via_loader",
                               return_value=[{"name": "llvmpipe (LLVM 21.1.8, 256 bits)", "type": "type 4", "software": True}]):
            ok, detail = dxvk.vulkan_ok()
            self.assertFalse(ok); self.assertIn("software rendering", detail)

    def test_no_vulkan_at_all_names_the_package(self):
        with mock.patch.object(dxvk.shutil, "which", return_value=None), \
             mock.patch.object(dxvk, "vulkan_devices", return_value=[]):
            ok, detail = dxvk.vulkan_ok()
            self.assertFalse(ok); self.assertIn("mesa-vulkan-drivers", detail)

class InstallTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); root = Path(self.tmp.name)
        self.p = Prefix(root / "prefix", WineBuild(root / "wine"))
        for sub in ("windows/system32", "windows/syswow64"):
            (self.p.drive_c / sub).mkdir(parents=True)
            for dll in dxvk.DLLS:                              # pretend Wine's own DLLs are there
                (self.p.drive_c / sub / f"{dll}.dll").write_bytes(b"wine builtin " + dll.encode())
        # a DXVK tarball with x64 and x32 directories, as the real release has
        for arch in ("x64", "x32"):
            src = root / f"src/dxvk-3.1/{arch}"; src.mkdir(parents=True)
            for dll in dxvk.DLLS: (src / f"{dll}.dll").write_bytes(f"DXVK {arch} {dll}".encode())
        self.tgz = root / "dxvk.tar.gz"
        with tarfile.open(self.tgz, "w:gz") as t: t.add(root / "src/dxvk-3.1", arcname="dxvk-3.1")
        self.reg: list[dict] = []
        self.p.reg_import_values = lambda values, name=None: self.reg.append(values) or 0

    def tearDown(self): self.tmp.cleanup()

    def _install(self, vulkan_stdout=VULKANINFO, **kw):
        w, s = _vulkaninfo(vulkan_stdout)
        with w, s, mock.patch.object(dxvk, "fetch", return_value=self.tgz), \
             mock.patch.object(dxvk.paths, "DOWNLOADS", Path(self.tmp.name) / "dl"):
            return dxvk.install(self.p, **kw)

    def test_installs_dlls_keeps_wine_backups_and_sets_overrides(self):
        res = self._install()
        self.assertTrue(res["installed"]); self.assertEqual(res["version"], dxvk.DXVK["version"])
        for arch, sub in dxvk.ARCHES:
            d = self.p.drive_c / sub
            for dll in dxvk.DLLS:
                self.assertEqual((d / f"{dll}.dll").read_bytes(), f"DXVK {arch} {dll}".encode())
                self.assertTrue((d / f"{dll}.dll{dxvk.BACKUP_SUFFIX}").read_bytes().startswith(b"wine builtin"))
        self.assertEqual(self.reg[0][r"HKCU\Software\Wine\DllOverrides"]["d3d11"], ("REG_SZ", "native,builtin"))
        self.assertEqual(dxvk.installed_version(self.p), dxvk.DXVK["version"])
        self.assertEqual(res["arches"], ["x64", "x32"]); self.assertTrue(dxvk.complete(self.p))

    def test_idempotent(self):
        self._install(); before = len(self.reg)
        res = self._install()
        self.assertEqual(res["reason"], "already installed"); self.assertEqual(len(self.reg), before)

    def test_a_prefix_with_only_the_64_bit_set_gets_the_32_bit_one(self):
        """Regression: the override is per DLL name, so with only system32 done
        a 32-bit program importing d3d9 could not start (Jamstix 4 Manager)."""
        self._install()
        (self.p.drive_c / dxvk.MARKER).write_text(json.dumps({"version": dxvk.DXVK["version"], "dlls": list(dxvk.DLLS)}))
        for dll in dxvk.DLLS: (self.p.drive_c / "windows/syswow64" / f"{dll}.dll").write_bytes(b"wine builtin " + dll.encode())
        self.assertFalse(dxvk.complete(self.p))
        res = self._install()
        self.assertEqual(res.get("reason"), None); self.assertIn("x32", res["arches"])
        self.assertTrue((self.p.drive_c / "windows/syswow64/d3d9.dll").read_bytes().startswith(b"DXVK x32"))
        self.assertTrue(dxvk.complete(self.p))

    def test_a_prefix_without_syswow64_is_complete_with_the_64_bit_set(self):
        import shutil; shutil.rmtree(self.p.drive_c / "windows/syswow64")
        res = self._install()
        self.assertEqual(res["arches"], ["x64"]); self.assertTrue(dxvk.complete(self.p))

    def test_skipped_without_hardware_vulkan_and_nothing_touched(self):
        res = self._install(SOFTWARE_ONLY)
        self.assertFalse(res["installed"]); self.assertIn("software rendering", res["reason"])
        self.assertTrue((self.p.drive_c / "windows/system32/d3d11.dll").read_bytes().startswith(b"wine builtin"))
        self.assertIsNone(dxvk.installed_version(self.p)); self.assertEqual(self.reg, [])

    def test_uninstall_restores_wine_dlls(self):
        self._install()
        self.assertTrue(dxvk.uninstall(self.p))
        for _, sub in dxvk.ARCHES:
            for dll in dxvk.DLLS:
                self.assertTrue((self.p.drive_c / sub / f"{dll}.dll").read_bytes().startswith(b"wine builtin"))
        self.assertEqual(self.reg[-1][r"HKCU\Software\Wine\DllOverrides"]["d3d11"], ("REG_SZ", "builtin"))
        self.assertIsNone(dxvk.installed_version(self.p))
        self.assertFalse(dxvk.uninstall(self.p))         # nothing left to do

if __name__ == "__main__": unittest.main()
