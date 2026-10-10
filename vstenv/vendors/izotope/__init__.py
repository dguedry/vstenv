"""iZotope: the Product Portal and the plugins it installs.

The Portal is an Electron application. Two things about it matter here, and
both are already handled by machinery this module only points at rather than
duplicates:

* Its installer keeps the Portal alive. The setup launches the Portal through
  `start.exe /exec` and leaves it running; under Wine the Portal's Electron
  main crashes, a supervisor relaunches it every few seconds, and that churn
  starves the shared wineserver until unrelated plugins deadlock -- an Ozone
  in the same prefix once sat in RtlpWaitForCriticalSection for 23 hours
  because of it. programs.install() stops anything the installer started that
  is still respawning (RESPAWNING_MANAGER names the Portal), so the loop ends
  with the install rather than outliving it.

* The Portal draws through Chromium, so it wants DXVK's Direct3D rather than
  this app's composition patches. It is named as a browser-drawn window below.

What this module adds is the inventory: which iZotope products are installed,
their versions, and whether the licence registry says they are authorised.
iZotope records that in a flat HKCU key rather than per product, so the
licence state is reported for the account, not claimed per plugin.
"""
from __future__ import annotations

import re
from pathlib import Path

from .. import Vendor, Product, Note, Check
from ...wine import Prefix

MANAGER = "Product Portal"
MANAGER_EXE = "iZotope Product Portal.exe"
IZOTOPE_REL = Path("Program Files/iZotope")
# iZotope keeps one flat key for the account, not one per product: Auth is the
# authorisation state, Challenge/Response the activation pair a licensed
# install has.
LICENCE_KEY = r"HKCU\Software\iZotope"


class IZotope(Vendor):
    id = "izotope"
    name = "iZotope"
    publisher = re.compile(r"izotope", re.I)
    manager_name = MANAGER
    download_page = "https://www.izotope.com/en/products/product-portal.html"
    installer_hint = "the iZotope Product Portal installer for Windows (.exe)"

    # The Portal's window is Chromium's, so it keeps DXVK rather than this
    # app's composition swapchains (see dcomp.py).
    def browser_window_exes(self): return (MANAGER_EXE,)

    def _manager(self, p: Prefix):
        from ... import programs
        return next((x for x in programs.installed(p)
                     if MANAGER.lower() in x.name.lower()
                     and self.publisher.search(x.publisher or "")), None)

    # -- the manager ------------------------------------------------------------------
    def manager_installed(self, p): return self._manager(p) is not None
    def manager_version(self, p):
        m = self._manager(p); return (m.version or None) if m else None
    def manager_exe_names(self): return (MANAGER_EXE,)

    def accepts_manager_installer(self, f):
        return bool(re.search(r"izotope.*product.?portal|product.?portal.*install", Path(f).name, re.I))

    def install_manager(self, p, installer, r=None):
        """Run the Portal's own installer.

        Nothing vendor-specific is needed: the generic path already ends the
        crash-looping Portal the installer leaves behind (see the module
        docstring), which is the one thing that used to hurt."""
        from ... import programs
        programs.install(p, installer, r)

    def launch_manager(self, p, r=None, args=()):
        from ... import programs
        m = self._manager(p)
        if m is None:
            raise RuntimeError(f"{MANAGER} is not installed yet — get it from {self.download_page} "
                               "and install it from the Install tab")
        return programs.run(p, m, r)

    def is_manager_program(self, prog):
        # By name alone, as the other vendors do: a program record can reach us
        # without a publisher (a Start Menu shortcut, say), and this decides
        # whether the Portal gets a desktop shortcut or has to be started
        # through the app. "Product Portal" is distinctive enough.
        return MANAGER.lower() in prog.name.lower()

    # -- what is installed --------------------------------------------------------------
    def _authorised(self, p: Prefix) -> bool | None:
        """Whether iZotope's licence key says this account is authorised.

        One flat key covers the account, so this is not per product: None when
        nothing has been recorded yet."""
        try: vals = p.reg_query(LICENCE_KEY)
        except Exception: return None
        if not vals: return None
        if vals.get("LastSentAnalyticsAuthState") == "Authorized": return True
        if vals.get("Challenge") and vals.get("Response"): return True
        if vals.get("Auth") in ("1", "2"): return True
        return None if "Auth" not in vals else False

    def products(self, p):
        """Installed iZotope products, from their own Uninstall records.

        The Portal's own store is a set of hash-named folders with nothing
        readable in them, so what is on disk is the reliable inventory."""
        from ... import programs
        auth = self._authorised(p)
        out = []
        for x in programs.installed(p):
            if not self.publisher.search(x.publisher or ""): continue
            if self.is_manager_program(x): continue
            out.append(Product(name=x.name, vendor=self.id, kind="Plugin",
                               version=x.version, install_dir=x.install_dir,
                               licensed=auth))
        return out

    def product_notes(self, p):
        return [Note(MANAGER, "patched",
                     "An Electron manager that its own installer leaves running, and whose Electron main "
                     "crashes under Wine: a supervisor relaunched it every few seconds, and the churn "
                     "starved the Wine session until unrelated plugins deadlocked. The app ends that loop "
                     "when the install finishes. Its window is Chromium's, so it draws through DXVK.")]

    # -- health -------------------------------------------------------------------------
    def checks(self, p):
        c = []
        m = self._manager(p)
        c.append(Check(f"{MANAGER} installed", m is not None, (m.version if m else "") or "",
                       fix=f"get it from {self.download_page}, then: vstenv manager izotope install <file>"))
        if m is not None:
            auth = self._authorised(p)
            c.append(Check("iZotope account authorised", auth is not False,
                           {True: "licence recorded in the prefix",
                            None: "no licence recorded yet — sign in through the Portal",
                            False: "the prefix records an unauthorised state"}[auth],
                           fix="" if auth is not False else f"open {MANAGER} and sign in"))
        return c


VENDOR = IZotope()
