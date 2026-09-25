"""IK Multimedia: the IK Product Manager and the plugins it installs.

The Product Manager is an ordinary Electron application whose installer runs
under Wine as is; it needs two small edits to its bundle (see quirks) and
--disable-gpu at launch, both of which the generic program machinery applies.
Its products register plain Uninstall entries, so they appear as programs; the
plugins land in the standard VST2/VST3 folders. Their JUCE GUIs draw through
Direct3D and need DXVK to repaint correctly (dxvk.py).
"""
from __future__ import annotations

import re
from pathlib import Path

from .. import Vendor, Product, Check, Quirk
from ... import asar, quirks
from ...wine import Prefix

MANAGER = "IK Product Manager"

# The Product Manager (Electron 11): its os-info module runs `ver` and expects
# "Microsoft Windows [Version 10.0.19045]"; Wine's cmd prints
# "Microsoft Windows 10.0.19045". The match is null, getVersion throws, and the
# same throw later breaks the app's API requests. Accept a bare version too.
_OS_INFO_OLD = rb"version = winVersion.match(/\[([^\]]+)\]/)"
_OS_INFO_NEW = rb"version = winVersion.match(/\[([^\]]+)\]/) || winVersion.match(/(\d+\.\d+[\d.]*)/) " + quirks.MARK

def os_info_edit(js: bytes):
    if quirks.MARK in js: return None
    if js.count(_OS_INFO_OLD) != 1: raise LookupError("os-info getVersion not found once")
    return js.replace(_OS_INFO_OLD, _OS_INFO_NEW, 1)

class IKMultimedia(Vendor):
    id = "ik"
    name = "IK Multimedia"
    publisher = re.compile(r"ik\s*multimedia", re.I)
    manager_name = MANAGER
    download_page = "https://www.ikmultimedia.com/products/productmanager/"
    installer_hint = "the IK Product Manager installer for Windows (.exe)"

    def _manager(self, p: Prefix):
        from ... import programs
        return next((x for x in programs.installed(p) if MANAGER.lower() in x.name.lower()), None)

    # -- IK Product Manager ------------------------------------------------------------
    def manager_installed(self, p): return self._manager(p) is not None
    def manager_version(self, p):
        m = self._manager(p); return (m.version or None) if m else None
    def manager_exe_names(self): return ("IK Product Manager.exe",)
    def accepts_manager_installer(self, f):
        return bool(re.search(r"ik[-_ ]?product[-_ ]?manager", Path(f).name, re.I))
    def install_manager(self, p, installer, r=None):
        from ... import programs
        programs.install(p, installer, r)          # its own window; quirks apply to what appeared
    def repair_manager(self, p, r=None):
        m = self._manager(p)
        if m and m.install_dir: quirks.apply(p, m.name, m.install_dir, r)
    def launch_manager(self, p, r=None, args=()):
        from ... import programs
        m = self._manager(p)
        if m is None: raise RuntimeError(f"{MANAGER} is not installed yet — get it from {self.download_page} and install it from the Install tab")
        return programs.run(p, m, r)
    def is_manager_program(self, prog): return MANAGER.lower() in prog.name.lower()

    # -- content ------------------------------------------------------------------------------
    def products(self, p):
        """IK products register ordinary Uninstall entries; that is the inventory."""
        from ... import programs
        return [Product(name=x.name, vendor=self.id, kind="App", version=x.version, install_dir=x.install_dir)
                for x in programs.installed(p) if self.publisher.search(x.publisher or "") and not self.is_manager_program(x)]
    def quirks(self):
        return {MANAGER: [Quirk("resources/app.asar", r"local_modules/os-info/index\.js", os_info_edit,
                                "os-info: accept Wine's `ver` output (no [Version …] brackets)"),
                          Quirk("resources/app.asar", r"^main\.js$", quirks.electron_disable_gpu,
                                "disable GPU acceleration in the app itself (also when a plugin starts it)")]}

    # -- health ------------------------------------------------------------------------------------
    def checks(self, p) -> list[Check]:
        from ... import dxvk
        c = []
        m = self._manager(p)
        c.append(Check("IK Product Manager installed", m is not None, (m.version if m else "") or "",
                       fix=f"get it from {self.download_page}, then: vstenv manager ik install <file>"))
        if m is not None and m.install_dir:
            a = p.to_host(m.install_dir) / "resources/app.asar"
            patched = a.exists() and quirks.MARK in a.read_bytes()
            c.append(Check("IK Product Manager quirks applied", patched, "" if patched else "its os-info and GPU fixes are missing: sign-in and product lists fail", fix="vstenv setup"))
        d = dxvk.status(p)
        if d["vulkan_ok"]:
            c.append(Check("IK plugin GUIs repaint (DXVK)", d["installed"], "" if d["installed"] else "without DXVK, IK's JUCE GUIs do not repaint until resized", fix="vstenv dxvk install"))
        return c

VENDOR = IKMultimedia()
