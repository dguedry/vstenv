"""Vendor modules: everything specific to one plugin vendor lives in one.

The core knows Wine, the prefix, yabridge and generic Windows programs. A
vendor module tells it about the vendor's own manager application (Native
Access, IK Product Manager, ...), the products that manager installs, the fixes
those need under Wine, and the health checks that matter for them. Modules are
found among the built-ins listed here and through the `vstenv.vendors`
entry-point group, so a vendor can be added by a separate package without
touching the core.

A module exposes `VENDOR`, an instance of a Vendor subclass. Every method has
a do-nothing default: a module implements only what its vendor needs.
"""
from __future__ import annotations

import importlib, re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, TYPE_CHECKING

if TYPE_CHECKING:
    import subprocess
    from ..wine import Prefix
    from ..programs import Program
    from ..progress import Reporter

@dataclass
class Product:
    """Something a vendor's manager installed: an application, a sound library, a bundle."""
    name: str
    vendor: str = ""
    kind: str = ""                 # "App" | "Content" | "Bundle" | "Utility" | ""
    version: str = ""
    install_dir: str = ""          # Windows paths
    content_dir: str = ""
    registered: bool = True        # the vendor's own software will find it
    licensed: bool | None = None   # None: this vendor does not expose licence state

@dataclass
class Check:
    """One health-check result (see doctor.py)."""
    name: str
    ok: bool
    detail: str = ""
    fix: str = ""                  # CLI command that repairs it

@dataclass
class Quirk:
    """A small edit inside a program's Electron bundle that makes it work under Wine."""
    asar: str                                  # relative to the program's install dir
    entry: str                                 # regex of the file inside the asar
    edit: Callable[[bytes], "bytes | None"]    # returns the new bytes, or None for "nothing to change"
    what: str                                  # one line for the step list

@dataclass
class UrlScheme:
    """A URL scheme the desktop must hand to a program in the prefix (browser sign-in callbacks)."""
    scheme: str                                # "native-access"
    title: str                                 # "Native Access login callback"
    argv: Callable[["Prefix"], list[str]]      # host command; the URL is appended as the last argument

class Vendor:
    id: str = ""                      # short, stable: "ni", "ik"
    name: str = ""                    # "Native Instruments"
    publisher: re.Pattern = re.compile(r"$^")   # matches the Publisher installers write
    manager_name: str | None = None   # the vendor's installer/manager application, if it has one
    download_page: str | None = None  # where the user gets that application
    installer_hint: str = ""          # what its installer file is called
    daemon_ports: tuple[int, ...] = () # localhost ports a vendor helper daemon binds (one per machine)

    # -- the manager application -------------------------------------------------
    def manager_installed(self, p: "Prefix") -> bool: return False
    def manager_version(self, p: "Prefix") -> str | None: return None
    def manager_notice(self, p: "Prefix") -> str | None:
        """Version advice for the user (newer available, known bad), or None."""
        return None
    def manager_exe_names(self) -> tuple[str, ...]: return ()
    def manager_running(self, p: "Prefix") -> bool:
        return any(p.is_running(x) for x in self.manager_exe_names())
    def accepts_manager_installer(self, f: Path) -> bool: return False
    def install_manager(self, p: "Prefix", installer: Path, r: "Reporter | None" = None): raise NotImplementedError
    def repair_manager(self, p: "Prefix", r: "Reporter | None" = None): pass
    def launch_manager(self, p: "Prefix", r: "Reporter | None" = None, args=()) -> "subprocess.Popen": raise NotImplementedError
    def is_manager_program(self, prog: "Program") -> bool:
        return bool(self.manager_name) and prog.name.lower() == self.manager_name.lower()

    # -- the prefix ------------------------------------------------------------------
    def prepare(self, p: "Prefix", r: "Reporter | None" = None):
        """Prefix-level things this vendor's software needs; idempotent. Runs at setup after the core."""

    # -- what is installed -----------------------------------------------------------------
    def products(self, p: "Prefix") -> list[Product]: return []
    def programs(self, p: "Prefix") -> list: return []        # extra Program sources (programs.py)
    def plugin_dirs(self) -> list[str]: return []              # prefix-relative dirs to bridge, beyond the standard ones
    def content_dirs(self, p: "Prefix") -> list[str]: return []   # Windows paths of libraries, maybe outside the prefix
    def quirks(self) -> dict[str, list[Quirk]]: return {}     # program name (substring) -> quirks
    def launch_args(self) -> dict[str, list[str]]: return {}  # program name (substring) -> extra argv
    def url_schemes(self, p: "Prefix") -> list[UrlScheme]: return []
    def logs(self, p: "Prefix") -> dict[str, Path]: return {} # name -> log file, for diagnostic reports

    # -- installing the vendor's products ----------------------------------------------------
    def accepts_product_installer(self, f: Path) -> bool: return False
    def install_product(self, p: "Prefix", installer: Path, r: "Reporter | None" = None, **kw) -> dict: raise NotImplementedError
    def watch_installs(self, p: "Prefix", watch=None):
        """While the manager runs: (watch, stalled, pids) for an install it drives that stopped making progress."""
        return watch, False, []
    def rescue_installs(self, p: "Prefix", r: "Reporter | None" = None) -> list[dict]: return []
    def staged_installs(self, p: "Prefix") -> list: return []
    def finish_installs(self, p: "Prefix", r: "Reporter | None" = None) -> list[dict]: return []
    def after_install(self, p: "Prefix", r: "Reporter | None" = None):
        """After anything was installed or removed (e.g. register libraries)."""

    # -- health and reports -------------------------------------------------------------------
    def checks(self, p: "Prefix") -> list[Check]: return []
    def status(self, p: "Prefix") -> dict: return {}

    def __repr__(self): return f"<Vendor {self.id}>"

# --- registry --------------------------------------------------------------------------
BUILTIN = ("vstenv.vendors.ni", "vstenv.vendors.ik")
ENTRY_POINT_GROUP = "vstenv.vendors"
_cache: list[Vendor] | None = None

def _as_vendor(obj) -> Vendor | None:
    if isinstance(obj, Vendor): return obj
    if isinstance(obj, type) and issubclass(obj, Vendor): return obj()
    v = getattr(obj, "VENDOR", None)
    return v if isinstance(v, Vendor) else None

def all() -> list[Vendor]:
    """Every vendor module: built-ins first, then entry points (a duplicate id loses)."""
    global _cache
    if _cache is not None: return _cache
    found: dict[str, Vendor] = {}
    for mod in BUILTIN:
        v = _as_vendor(importlib.import_module(mod))
        if v is not None: found[v.id] = v
    try:
        from importlib.metadata import entry_points
        eps = list(entry_points(group=ENTRY_POINT_GROUP))
    except Exception: eps = []
    for ep in eps:
        try: v = _as_vendor(ep.load())
        except Exception: continue
        if v is not None and v.id not in found: found[v.id] = v
    _cache = list(found.values())
    return _cache

def get(vid: str) -> Vendor:
    for v in all():
        if v.id == vid or v.name.lower() == vid.lower(): return v
    raise LookupError(f"no vendor module '{vid}' (have: {', '.join(v.id for v in all())})")

def for_manager_installer(f: Path) -> Vendor | None:
    return next((v for v in all() if v.accepts_manager_installer(Path(f))), None)

def for_product_installer(f: Path) -> Vendor | None:
    return next((v for v in all() if v.accepts_product_installer(Path(f))), None)

def for_program(prog: "Program") -> Vendor | None:
    """The vendor a program belongs to, by its Publisher or by being that vendor's manager."""
    for v in all():
        if v.is_manager_program(prog): return v
    for v in all():
        if prog.publisher and v.publisher.search(prog.publisher): return v
        if prog.name.lower().startswith(v.name.lower() + " "): return v
    return None

def with_manager() -> list[Vendor]:
    return [v for v in all() if v.manager_name]
