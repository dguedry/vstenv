"""Setting the environment up and keeping it consistent: the core steps, then
every vendor module's own, in one idempotent pass."""
from pathlib import Path
from . import paths, runtime, dxvk, yabridge, menu, urlschemes, vendors, tools, winefixes, dcomp, desktopfix, webview2, programs
from .progress import null_reporter
from .wine import Prefix

def _guarded(r, title, fn):
    try: fn()
    except Exception as e: r.step(title); r.fail(str(e)[:100])

def prepare(p: Prefix, reporter=None):
    """Everything that does not need a vendor's manager: prefix, fonts, C runtime,
    patched Wine DLLs, DXVK, yabridge, then each vendor's own prerequisites. Safe to re-run."""
    r = null_reporter(reporter)
    _guarded(r, "7-Zip", lambda: tools.ensure(r))
    p.create(r); p.declare_wine(r); p.refresh_builtins(r); runtime.install(p, r)
    _guarded(r, "Wine fixes (patched DLLs)", lambda: winefixes.install(p.build, r))   # dcomp for Steinberg; needs the release asset
    _guarded(r, "Installing DXVK", lambda: dxvk.install(p, r))     # skipped without a hardware Vulkan driver
    p.wait_idle()
    _guarded(r, "Installing yabridge", lambda: yabridge.install(r))
    _guarded(r, "nilinux leftovers", lambda: yabridge.remove_nilinux_leftovers(r))
    _guarded(r, "Plugin DLL overrides", lambda: yabridge.write_plugin_overrides(dcomp.all_plugin_overrides(p)))
    _guarded(r, "DirectComposition programs", lambda: dcomp.apply_program_overrides(p, dcomp.program_exes(p), r))
    _guarded(r, "WebView2 presentation flags", lambda: webview2.apply_presentation_flags(p, r))
    for v in vendors.all():
        _guarded(r, f"{v.name}: preparing", lambda v=v: v.prepare(p, r))
    _guarded(r, "URL handlers", lambda: urlschemes.register_all(p, r))
    _guarded(r, "Desktop window behaviour", lambda: desktopfix.apply(r))   # Cinnamon: stop Wine windows raising themselves
    _guarded(r, "Desktop menu entries", lambda: menu.sync(p, r))

def setup(p: Prefix, reporter=None, installer: Path | None = None):
    """prepare(); then install the vendor manager an installer belongs to, or
    re-apply every installed manager's fixes (repair)."""
    r = null_reporter(reporter)
    prepare(p, r)
    changed = False
    if installer:
        v = vendors.for_manager_installer(installer)
        if v is None: raise LookupError(f"{Path(installer).name} is not a manager installer of any vendor module")
        v.install_manager(p, Path(installer), r); changed = True
    else:
        for v in vendors.with_manager():
            if v.manager_installed(p):
                _guarded(r, f"{v.manager_name}: repair", lambda v=v: v.repair_manager(p, r)); changed = True
    if changed: _guarded(r, "Desktop menu entries", lambda: menu.sync(p, r))   # prepare() already synced once

STAMP = paths.DATA / "last-run-version"

def needs_upgrade_pass() -> bool:
    """True on the first run after the app's version changed: the code ships new
    fixes (patched Wine DLLs, launcher blocks, registry keys) that only land when
    prepare() runs, and a ready-looking environment would otherwise keep the old
    ones until a manual repair."""
    from . import __version__
    try: return STAMP.read_text().strip() != __version__
    except OSError: return True

def mark_version_ran():
    from . import __version__
    try: STAMP.parent.mkdir(parents=True, exist_ok=True); STAMP.write_text(__version__)
    except OSError: pass

def upgrade_pass(p: Prefix, reporter=None):
    """prepare() is idempotent and cheap when everything is in place; running it on
    the first launch after an update is how existing users pick up new fixes
    without knowing to press repair."""
    prepare(p, reporter)
    mark_version_ran()

CHANGE_SIG = paths.DATA / "after-change.sig"

def change_signature(p: Prefix) -> str:
    """A cheap fingerprint of everything the after-install pass reacts to:
    the program tree (two levels), every plugin directory in full, NI's
    installed-products records and the vendors' staged downloads. Host-side
    stats only -- no Wine calls -- so it costs well under a second."""
    import hashlib, os
    h = hashlib.sha1()
    def feed(s: str): h.update(s.encode(errors="replace")); h.update(b"\0")
    for base in ("Program Files", "Program Files (x86)"):
        root = p.drive_c / base
        if not root.is_dir(): continue
        for d in sorted(root.iterdir()):
            try: feed(f"{d.name}:{d.stat().st_mtime_ns}")
            except OSError: continue
            if d.is_dir():
                for sub in sorted(d.iterdir()):
                    try: feed(f"{d.name}/{sub.name}:{sub.stat().st_mtime_ns}")
                    except OSError: pass
    for rel in yabridge.STANDARD_DIRS + [d for v in vendors.all() for d in v.plugin_dirs()]:
        for dirpath, dirs, files in os.walk(p.drive_c / rel):
            dirs.sort(); feed(dirpath)
            for f in sorted(files):
                try: st = os.stat(os.path.join(dirpath, f)); feed(f"{f}:{st.st_size}:{st.st_mtime_ns}")
                except OSError: pass
    ipd = p.public_docs / "Native Instruments/installed_products"
    if ipd.is_dir():
        for f in sorted(ipd.glob("*.json")):
            try: feed(f"{f.name}:{f.stat().st_mtime_ns}")
            except OSError: pass
    for v in vendors.all():
        try:
            for name, _src in v.staged_installs(p): feed(f"staged:{v.id}:{name}")
        except Exception: pass
    return h.hexdigest()

def after_change(p: Prefix, reporter=None, force=False) -> dict:
    """The epilogue of every install or removal: vendor bookkeeping (libraries
    registered, ...), plugins bridged, menu entries current. Skipped outright
    when nothing it reacts to has changed since the last completed pass -- a
    manager opened and closed without installing anything used to cost the full
    ten-second pass anyway."""
    r = null_reporter(reporter)
    programs.invalidate()      # an install or removal just changed what is there
    try: sig = change_signature(p)
    except Exception: sig = None
    if not force and sig is not None:
        try:
            if CHANGE_SIG.read_text() == sig:
                r.step("After-install bookkeeping")
                r.skip("nothing changed since the last pass")
                return {"skipped": True}
        except OSError: pass
    for v in vendors.all():
        _guarded(r, f"{v.name}: after install", lambda v=v: v.after_install(p, r))
    _guarded(r, "DirectComposition programs", lambda: dcomp.apply_program_overrides(p, dcomp.program_exes(p), r))
    # A WebView2 app just installed (SINE, Audio Modeling) crashes at startup without
    # a runtime; install one on demand so it launches. It may still not render its
    # embedded-Edge UI under Wine, but launching beats crashing.
    if webview2.needed(p):
        _guarded(r, "Microsoft WebView2 runtime", lambda: webview2.install(p, r))
    _guarded(r, "WebView2 presentation flags", lambda: webview2.apply_presentation_flags(p, r))
    _guarded(r, "Plugin DLL overrides", lambda: yabridge.write_plugin_overrides(dcomp.all_plugin_overrides(p)))
    res = yabridge.sync(p, r)
    _guarded(r, "Desktop menu entries", lambda: menu.sync(p, r))
    try:   # the pass itself changes things (bridges, menu): fingerprint the result
        CHANGE_SIG.parent.mkdir(parents=True, exist_ok=True)
        CHANGE_SIG.write_text(change_signature(p))
    except Exception: pass
    return res
