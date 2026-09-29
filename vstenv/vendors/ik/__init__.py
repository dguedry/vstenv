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

from .. import Vendor, Product, Check, Quirk, Note
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

# The Product Manager loads its UI live from ikmultimedia.com with nodeIntegration
# on, and the page calls shell.openExternal() for its own in-app navigation (the
# logo, My Products, My Orders, the user area, support and legal links). Under a
# desktop browser each such click opens a new tab, so ordinary use of the app
# sprays ikmultimedia.com tabs and the window churns to the front as the browser
# comes and goes. This wraps shell.openExternal in the main process so a link to
# IK's own site loads in the app's own window instead; anything else (a YouTube
# tutorial, say) still opens in the browser.
_OPENEXT_MARK = b"/*VSTENV_OPENEXT*/"
_ELECTRON_REQUIRE_IK = quirks._ELECTRON_REQUIRE
def keep_ik_links_in_window(js: bytes):
    if _OPENEXT_MARK in js: return None
    m = _ELECTRON_REQUIRE_IK.search(js)
    if not m: raise LookupError("no `require('electron')` line in the main script")
    shim = (b"try{const _e=require('electron');const _o=_e.shell.openExternal.bind(_e.shell);"
            b"_e.shell.openExternal=function(u,opts){try{if(typeof u==='string'&&/^https?:\\/\\/([a-z0-9-]+\\.)*ikmultimedia\\.com(\\/|$)/i.test(u)){"
            b"const w=_e.BrowserWindow.getAllWindows()[0];if(w){w.loadURL(u);return Promise.resolve();}}}catch(e){}return _o(u,opts);};}catch(e){} "
            + _OPENEXT_MARK + b"\n")
    return js[:m.end()] + shim + js[m.end():]

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
    def product_notes(self, p):
        from ... import dxvk
        try: gpu = dxvk.status(p)
        except Exception: gpu = {"installed": False}
        return [Note(MANAGER, "patched", "IK copied NI's Electron storefront and its mistakes, then added one of its own: it refuses to run because "
                     "the output of `ver` does not look like a Windows it has met. Two edits to its bundle and --disable-gpu, and it behaves. "
                     "Its login wants your IK username, not the email you registered with; it says 'invalid password' either way."),
                Note("", "works" if gpu["installed"] else "limited",
                     "JUCE plugin GUIs that draw through Direct3D; they repaint through DXVK here." if gpu["installed"] else
                     "JUCE plugin GUIs that draw through Direct3D and need DXVK to repaint; this machine has no usable Vulkan driver, "
                     "so they may only repaint when resized.")]
    def products(self, p):
        """IK products register ordinary Uninstall entries; that is the inventory."""
        from ... import programs
        return [Product(name=x.name, vendor=self.id, kind="App", version=x.version, install_dir=x.install_dir)
                for x in programs.installed(p) if self.publisher.search(x.publisher or "") and not self.is_manager_program(x)]
    # -- finishing the Product Manager's downloads -------------------------------------------
    # The Product Manager downloads a product into
    # Documents/IK Multimedia/IK Product Manager/<Product>/ (a .zip and the .exe
    # it unpacks) and then tries to launch the installer with Node's exec, which
    # wraps the already-quoted path in another pair of quotes -- Wine's cmd
    # rejects `cmd /c ""C:\...\Install X (1.2.3).exe""` (worse with the
    # parentheses in IK's names). The install fails, the page retries, and each
    # retry opens a browser tab (shell.openExternal). vstenv runs the downloaded
    # installer itself, through its own installer path, which quotes correctly and
    # waits for it. Products already installed are left alone.
    def _downloads_dir(self, p: Prefix) -> Path:
        return p.user_dir / "Documents/IK Multimedia/IK Product Manager"

    def _installer_from(self, sub: Path, reporter=None) -> Path | None:
        """The product installer .exe in a download folder, extracted from the
        folder's .zip ourselves when needed. The Product Manager's own extraction
        can stop partway under Wine, leaving a truncated .exe whose InstallAware
        integrity check then fails with "the setup files are corrupted"; the .zip
        is the source of truth, so if it lists an .exe we extract it (streamed)
        and use it when its size does not already match on disk."""
        import zipfile
        from ...progress import null_reporter
        rr = null_reporter(reporter)
        zips = sorted(sub.glob("*.zip"))
        for z in zips:
            try:
                zf = zipfile.ZipFile(z)
                info = next((i for i in zf.infolist() if i.filename.lower().endswith(".exe")
                             and "uninstall" not in i.filename.lower()), None)
            except zipfile.BadZipFile:
                rr.log(f"{z.name}: not a complete zip yet; skipped"); continue
            if info is None: continue
            dst = sub / Path(info.filename).name
            if dst.exists() and dst.stat().st_size == info.file_size: return dst   # already complete
            rr.step(f"Extracting {dst.name} ({info.file_size // 1_000_000} MB)")
            tmp = sub / f".{dst.name}.new"
            try:
                with zf.open(info) as src, open(tmp, "wb") as out:
                    n = 0
                    while True:
                        b = src.read(1 << 20)
                        if not b: break
                        out.write(b); n += len(b)
                if n != info.file_size:
                    rr.fail(f"extracted {n} of {info.file_size} bytes"); tmp.unlink(missing_ok=True); continue
                tmp.replace(dst); rr.ok()
                return dst
            except Exception as e:
                rr.fail(str(e)[:80]); tmp.unlink(missing_ok=True); continue
        # no usable zip: fall back to an .exe on disk only if it is not obviously partial
        exe = next((x for x in sorted(sub.glob("*.exe")) if "uninstall" not in x.name.lower()), None)
        return exe

    def staged_installs(self, p: Prefix) -> list:
        """Downloaded IK product installers that are present but whose product is
        not installed yet: [(product name, folder)]. The installer .exe is
        resolved (and re-extracted if truncated) when the install runs."""
        from ... import programs
        d = self._downloads_dir(p)
        if not d.is_dir(): return []
        installed = {x.name.lower() for x in programs.installed(p)}
        out = []
        for sub in sorted(d.iterdir()):
            if not sub.is_dir(): continue
            if any(sub.name.lower() in n or n in sub.name.lower() for n in installed): continue
            if any(sub.glob("*.zip")) or any(x for x in sub.glob("*.exe") if "uninstall" not in x.name.lower()):
                out.append((sub.name, sub))
        return out

    def finish_installs(self, p: Prefix, r=None) -> list[dict]:
        """Run each downloaded IK installer the Product Manager could not launch,
        extracting it correctly from its zip first (the Product Manager can leave a
        truncated exe that fails its own integrity check)."""
        from ... import programs
        from ...progress import null_reporter
        rr = null_reporter(r); done = []
        for name, sub in self.staged_installs(p):
            rr.step(f"Finishing IK download: {name}")
            exe = self._installer_from(sub, rr)
            if exe is None:
                rr.fail("no usable installer in the download folder")
                done.append({"vendor": self.id, "product": name, "ok": False}); continue
            try:
                rc = programs.install(p, exe, rr)
                done.append({"vendor": self.id, "product": name, "ok": rc == 0})
            except Exception as e:
                rr.fail(str(e)[:100]); done.append({"vendor": self.id, "product": name, "ok": False})
        return done

    def quirks(self):
        return {MANAGER: [Quirk("resources/app.asar", r"local_modules/os-info/index\.js", os_info_edit,
                                "os-info: accept Wine's `ver` output (no [Version …] brackets)"),
                          Quirk("resources/app.asar", r"^main\.js$", quirks.electron_disable_gpu,
                                "disable GPU acceleration in the app itself (also when a plugin starts it)"),
                          Quirk("resources/app.asar", r"^main\.js$", keep_ik_links_in_window,
                                "keep IK's own in-app links in the window (its page opens a browser tab per click)")]}

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
