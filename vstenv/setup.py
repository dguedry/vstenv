"""Setting the environment up and keeping it consistent: the core steps, then
every vendor module's own, in one idempotent pass."""
from pathlib import Path
from . import runtime, dxvk, yabridge, menu, urlschemes, vendors
from .progress import null_reporter
from .wine import Prefix

def _guarded(r, title, fn):
    try: fn()
    except Exception as e: r.step(title); r.fail(str(e)[:100])

def prepare(p: Prefix, reporter=None):
    """Everything that does not need a vendor's manager: prefix, fonts, C runtime,
    DXVK, yabridge, then each vendor's own prerequisites. Safe to re-run."""
    r = null_reporter(reporter)
    p.create(r); p.declare_wine(r); p.refresh_builtins(r); runtime.install(p, r)
    _guarded(r, "Installing DXVK", lambda: dxvk.install(p, r))     # skipped without a hardware Vulkan driver
    p.wait_idle()
    _guarded(r, "Installing yabridge", lambda: yabridge.install(r))
    for v in vendors.all():
        _guarded(r, f"{v.name}: preparing", lambda v=v: v.prepare(p, r))
    _guarded(r, "URL handlers", lambda: urlschemes.register_all(p, r))
    _guarded(r, "Desktop menu entries", lambda: menu.sync(p, r))

def setup(p: Prefix, reporter=None, installer: Path | None = None):
    """prepare(); then install the vendor manager an installer belongs to, or
    re-apply every installed manager's fixes (repair)."""
    r = null_reporter(reporter)
    prepare(p, r)
    if installer:
        v = vendors.for_manager_installer(installer)
        if v is None: raise LookupError(f"{Path(installer).name} is not a manager installer of any vendor module")
        v.install_manager(p, Path(installer), r)
    else:
        for v in vendors.with_manager():
            if v.manager_installed(p): _guarded(r, f"{v.manager_name}: repair", lambda v=v: v.repair_manager(p, r))
    _guarded(r, "Desktop menu entries", lambda: menu.sync(p, r))

def after_change(p: Prefix, reporter=None) -> dict:
    """The epilogue of every install or removal: vendor bookkeeping (libraries
    registered, ...), plugins bridged, menu entries current."""
    r = null_reporter(reporter)
    for v in vendors.all():
        _guarded(r, f"{v.name}: after install", lambda v=v: v.after_install(p, r))
    res = yabridge.sync(p, r)
    _guarded(r, "Desktop menu entries", lambda: menu.sync(p, r))
    return res
