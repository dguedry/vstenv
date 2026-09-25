"""Steinberg: the Download Assistant and what it installs.

The Steinberg Download Assistant (SDA) is a JavaFX application with its own
Java runtime. Two things break under Wine (found with SDA 1.40.1, 2026-09-25):

- Text renders as blobs. JavaFX rasterises glyphs through DirectWrite into a
  Direct2D render target; Wine's Direct2D draws through D3D11, and with DXVK
  serving D3D11 every glyph fails (DWGlyph.createRenderingTarget throws a
  NullPointerException, 300 times per screen). Giving this one executable
  Wine's built-in D3D11, DXGI, D3D10 and D3D9 through a per-application
  DllOverrides key makes the text render; DXVK stays in place for the plugins.
- Sign-in never comes back. SDA opens the Steinberg ID login in the system
  browser; the flow page ends by opening a net-steinberg-sda:// link, which
  starts a second SDA instance that hands the URL to the running one over its
  IPC. The host browser needs a handler for that scheme (urlschemes.py); the
  page's "Try Again" then completes the login.

SDA installs its runtime components (Activation Manager, Library Manager,
built-in ASIO driver, MediaBay) and every product through the Steinberg
Install Assistant and its Install Helper, which are .NET executables: with
mscoree disabled they die with STATUS_DLL_NOT_FOUND and SDA reports "failed
to update Steinberg Library Manager". Under Wine Mono (mono.py) they run and
the components install; MediaBay's installer still exits 231. Their installer
packages are RAR self-extractors (7-Zip 24+ opens them) holding a .NET
SetupBootstrapper and an MSI; the MSI installs fine with msiexec directly.
"""
from __future__ import annotations

import re
from pathlib import Path

from .. import Vendor, Product, Check, UrlScheme
from ...progress import null_reporter
from ... import mono
from ...installers import steinberg as pkg
from ...wine import Prefix

MANAGER = "Steinberg Download Assistant"
EXE = "Steinberg Download Assistant.exe"
DEFAULT_EXE = r"C:\Program Files (x86)\Steinberg\Download Assistant\Steinberg Download Assistant.exe"
OVERRIDES = ("d3d9", "d3d10core", "d3d11", "dxgi")
OVERRIDES_KEY = rf"HKCU\Software\Wine\AppDefaults\{EXE}\DllOverrides"
SCHEME = "net-steinberg-sda"

def text_fix_applied(p: Prefix) -> bool:
    cur = p.reg_query(OVERRIDES_KEY)
    return all(cur.get(d) == "builtin" for d in OVERRIDES)

def apply_text_fix(p: Prefix, reporter=None) -> bool:
    """Returns True if something was changed."""
    r = null_reporter(reporter)
    r.step(f"{MANAGER}: text rendering (built-in Direct3D for this app, DXVK elsewhere)")
    cur = p.reg_query(OVERRIDES_KEY)
    missing = [d for d in OVERRIDES if cur.get(d) != "builtin"]
    if not missing: r.skip("already"); return False
    for d in missing: p.reg_add(OVERRIDES_KEY, d, "builtin")
    r.ok(", ".join(missing) + " = builtin"); return True

class Steinberg(Vendor):
    id = "steinberg"
    name = "Steinberg"
    publisher = re.compile(r"steinberg", re.I)
    manager_name = MANAGER
    download_page = "https://o.steinberg.net/en/support/downloads/steinberg_download_assistant.html"
    installer_hint = "the Steinberg Download Assistant installer for Windows (.exe)"

    def _manager(self, p: Prefix):
        from ... import programs
        return next((x for x in programs.installed(p) if "download assistant" in x.name.lower()), None)
    def _exe(self, p: Prefix) -> str:
        m = self._manager(p)
        return m.exe if m and m.exe else DEFAULT_EXE

    # -- Download Assistant --------------------------------------------------------------
    def manager_installed(self, p): return self._manager(p) is not None
    def manager_version(self, p):
        m = self._manager(p); return (m.version or None) if m else None
    def manager_exe_names(self): return (EXE,)
    def accepts_manager_installer(self, f):
        return bool(re.search(r"steinberg[-_ ]?download[-_ ]?assistant", Path(f).name, re.I))
    def install_manager(self, p, installer, r=None):
        from ... import programs
        programs.install(p, installer, r)          # its own window
        self.fixes(p, r)
    def repair_manager(self, p, r=None): self.fixes(p, r)
    def fixes(self, p, r=None):
        apply_text_fix(p, r)
        mono.install(p, r)                         # the Install Assistant it drives is .NET
    def launch_manager(self, p, r=None, args=()):
        from ... import programs
        m = self._manager(p)
        if m is None: raise RuntimeError(f"{MANAGER} is not installed yet — get it from {self.download_page} and install it from the Install tab")
        apply_text_fix(p, r)
        return programs.run(p, m, r)
    def is_manager_program(self, prog): return "download assistant" in prog.name.lower()

    # -- content ------------------------------------------------------------------------------
    def products(self, p):
        """Steinberg products register ordinary Uninstall entries; that is the inventory."""
        from ... import programs
        return [Product(name=x.name, vendor=self.id, kind="App", version=x.version, install_dir=x.install_dir)
                for x in programs.installed(p) if self.publisher.search(x.publisher or "") and not self.is_manager_program(x)]
    def url_schemes(self, p):
        return [UrlScheme(SCHEME, f"{MANAGER} login callback", lambda p: [str(p.build.wine), str(p.to_host(self._exe(p)))])]
    def logs(self, p):
        d = p.user_dir / "AppData/Local/Steinberg Download Assistant/logs"
        latest = max(d.glob("*.log"), key=lambda f: f.stat().st_mtime, default=None) if d.is_dir() else None
        return {"steinberg-download-assistant.log": latest} if latest else {}

    # -- installs ------------------------------------------------------------------------------
    # The Download Assistant hands every package to the .NET Install Assistant, which
    # refuses any with a signed prerun script under Wine (no PowerShell signature
    # provider). Those are finished here from the package it leaves in Temp.
    def accepts_product_installer(self, f): return pkg.is_package(f)
    def install_product(self, p, installer, r=None, **kw): return pkg.install(p, Path(installer), r)
    def staged_installs(self, p): return pkg.staged(p)
    def finish_installs(self, p, r=None): return pkg.finish_staged(p, r)
    def rescue_installs(self, p, r=None): return pkg.finish_staged(p, r)

    # -- health ------------------------------------------------------------------------------------
    def checks(self, p) -> list[Check]:
        c = []
        m = self._manager(p)
        c.append(Check(f"{MANAGER} installed", m is not None, (m.version if m else "") or "",
                       fix=f"get it from {self.download_page}, then: vstenv manager steinberg install <file>"))
        if m is not None:
            ok = text_fix_applied(p)
            c.append(Check(f"{MANAGER} text renders (built-in Direct3D)", ok, "" if ok else "its text draws as blobs under DXVK's D3D11",
                           fix="vstenv manager steinberg repair"))
            has = mono.installed(p)
            c.append(Check("Wine Mono (.NET) for the Steinberg Install Assistant", has, "" if has else "runtime components and products fail to install without it",
                           fix="vstenv manager steinberg repair"))
            left = pkg.staged(p)
            c.append(Check("no Steinberg installs left unfinished", not left, ", ".join(x.name for x in left), fix="vstenv finish-installs"))
        return c
    def status(self, p) -> dict:
        m = self._manager(p)
        return {"installed": m is not None, "version": m.version if m else None, "text_fix": text_fix_applied(p) if m else False, "mono": mono.installed(p)}

VENDOR = Steinberg()
