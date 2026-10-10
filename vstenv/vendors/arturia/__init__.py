"""Arturia: the Software Center and the instruments it installs.

The Software Center (ASC) runs under Wine as it is. One thing about it needed
handling, and the generic machinery already does it: its installer refuses to
touch files a running yabridge host has mapped ("Cannot start installation.
Files are in use."), which is why a vendor manager is started through this app
rather than from a desktop shortcut, and why doctor reports the plugins a DAW
currently holds open (yabridge.plugins_in_use).

What this module adds is the inventory. Arturia's registry keeps only install
directories, and ASC's scanner.json is a 170-rule detection ruleset rather than
a record of anything owned -- but ASC writes what it has installed, with
versions, to its own user/installed.log, which is the honest source for "what
is installed". Licences live in lic.ascd, which is binary and not read here, so
no licence state is claimed.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .. import Vendor, Product, Note, Check
from ...wine import Prefix

MANAGER = "Arturia Software Center"
MANAGER_EXE = "Arturia Software Center.exe"
# ASC's own record of what it installed, with versions.
INSTALLED_LOG = Path("ProgramData/ArturiaSC/Arturia Software Center/user/installed.log")
# ASC's product_id for itself; not an instrument.
_SELF_ID = "-2"


class Arturia(Vendor):
    id = "arturia"
    name = "Arturia"
    publisher = re.compile(r"arturia", re.I)
    manager_name = MANAGER
    download_page = "https://www.arturia.com/support/downloads&manuals"
    installer_hint = "the Arturia Software Center installer for Windows (.exe)"

    def _manager(self, p: Prefix):
        from ... import programs
        return next((x for x in programs.installed(p) if MANAGER.lower() in x.name.lower()), None)

    # -- the manager -------------------------------------------------------------------
    def manager_installed(self, p): return self._manager(p) is not None
    def manager_version(self, p):
        m = self._manager(p); return (m.version or None) if m else None
    def manager_exe_names(self): return (MANAGER_EXE,)

    def accepts_manager_installer(self, f):
        return bool(re.search(r"arturia.*software.?center|^asc[-_ ]?setup", Path(f).name, re.I))

    def install_manager(self, p, installer, r=None):
        """Run ASC's own installer.

        Nothing vendor-specific: its one Wine problem is refusing to replace
        files a running plugin host has mapped, which is about what else is
        running rather than about the installer."""
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
        return MANAGER.lower() in prog.name.lower()

    # -- what is installed ---------------------------------------------------------------
    def _installed_log(self, p: Prefix) -> list[dict]:
        """ASC's own list of installed products. [] when it has not written one."""
        f = p.drive_c / INSTALLED_LOG
        try: d = json.loads(f.read_text(encoding="utf-8", errors="replace"))
        except (OSError, ValueError): return []
        items = d.get("installed")
        return items if isinstance(items, list) else []

    def products(self, p):
        """Arturia instruments, from ASC's own record where it has one.

        ASC knows its products by name and version even when they register no
        ordinary Uninstall entry, so its log is preferred; anything Arturia
        published that the log misses is picked up from the installed programs.
        No licence state: Arturia keeps that in a binary lic.ascd."""
        from ... import programs
        out, seen = [], set()
        for item in self._installed_log(p):
            name = (item.get("product_name") or "").strip()
            if not name or str(item.get("product_id")) == _SELF_ID: continue
            seen.add(name.lower())
            out.append(Product(name=name, vendor=self.id, kind="Instrument",
                               version=(item.get("version") or "").strip()))
        for x in programs.installed(p):
            if not self.publisher.search(x.publisher or ""): continue
            if self.is_manager_program(x): continue
            base = re.sub(r"\s+[\d.]+$", "", x.name).strip()       # "Analog Lab V 5.12.5" -> "Analog Lab V"
            if base.lower() in seen or x.name.lower() in seen: continue
            seen.add(base.lower())
            out.append(Product(name=base or x.name, vendor=self.id, kind="Instrument",
                               version=x.version, install_dir=x.install_dir))
        return sorted(out, key=lambda x: x.name.lower())

    def product_notes(self, p):
        return [Note(MANAGER, "works",
                     "Runs under Wine as it is. Its installer will not replace a plugin a DAW has loaded "
                     "(\"Cannot start installation. Files are in use.\"), so close the DAW before installing "
                     "or updating -- Health lists what is currently held open.")]

    # -- health ---------------------------------------------------------------------------
    def checks(self, p):
        c = []
        m = self._manager(p)
        c.append(Check(f"{MANAGER} installed", m is not None, (m.version if m else "") or "",
                       fix=f"get it from {self.download_page}, then: vstenv manager arturia install <file>"))
        if m is not None:
            n = len([x for x in self._installed_log(p) if str(x.get("product_id")) != _SELF_ID])
            c.append(Check("Arturia products recorded", True,
                           f"{n} in the Software Center's own list" if n else
                           "none recorded yet — the Software Center writes this when it installs something"))
        return c


VENDOR = Arturia()
