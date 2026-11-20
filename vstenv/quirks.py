"""Per-program fixes for Windows programs that need a nudge under Wine.

A quirk names a program (as the Programs page lists it), says how to tell
whether it applies, and applies a small change to the program's Electron
bundle. Quirks run after "Install a Windows program" and before every Run, and
are idempotent. Vendor modules declare them (Vendor.quirks); this module only
knows how to apply one, plus the fixes every Electron app needs.
"""
import re
from .asar import patch as asar_patch
from .progress import null_reporter
from .wine import Prefix

MARK = b"/*VSTENV_QUIRK*/"

# Electron's GPU process wedges under Wine and the browser thread waits on it
# forever: a blank window the desktop reports as "not responding". The launcher
# passes --disable-gpu, but a plugin may start the vendor's manager itself from a
# licence dialog, with no arguments. Bake the same thing into the app:
# disableHardwareAcceleration() right after Electron is required, before "ready".
_ELECTRON_REQUIRE = re.compile(rb"^.*=\s*require\((['\"])electron\1\)[^\n]*\n", re.M)

def electron_disable_gpu(js: bytes):
    if MARK in js: return None
    m = _ELECTRON_REQUIRE.search(js)
    if not m: raise LookupError("no `require('electron')` line in the main script")
    inject = b"require('electron').app.disableHardwareAcceleration() " + MARK + b"\n"
    return js[:m.end()] + inject + js[m.end():]

# Extra command-line arguments per program. Electron apps get --disable-gpu by
# default (see above). An app whose own argument parser rejects unknown options
# can be listed by a vendor with [] instead (Vendor.launch_args).
ELECTRON_ARGS = ["--disable-gpu"]
LAUNCH_ARGS: dict[str, list[str]] = {}     # overrides added at runtime (tests, power users)

def is_electron(p: Prefix, install_dir: str) -> bool:
    d = p.to_host(install_dir)
    return (d / "resources/app.asar").exists() or (d / "resources/electron.asar").exists() or (d / "chrome_100_percent.pak").exists()

def _vendor_launch_args() -> dict[str, list[str]]:
    from . import vendors
    out = {}
    for v in vendors.all(): out.update(v.launch_args())
    return out

def launch_args(p: Prefix, name: str, install_dir: str) -> list[str]:
    """Arguments to add when starting `name` from install_dir (Windows path)."""
    for table in (LAUNCH_ARGS, _vendor_launch_args()):
        for key, args in table.items():
            if key.lower() in name.lower(): return list(args)
    if install_dir and is_electron(p, install_dir): return list(ELECTRON_ARGS)
    return []

def _match(name: str) -> list:
    from . import vendors
    for v in vendors.all():
        for key, qs in v.quirks().items():
            if key.lower() in name.lower(): return list(qs)
    return []

def apply(p: Prefix, name: str, install_dir: str, reporter=None) -> list[str]:
    """Apply the quirks registered for `name` to the program under install_dir
    (Windows path). Returns what was done."""
    r = null_reporter(reporter); done = []
    for q in _match(name):
        a = p.to_host(install_dir) / q.asar
        if not a.exists(): continue
        r.step(f"Quirk for {name}: {q.what}")
        try: res = asar_patch(a, {q.entry: q.edit})
        except LookupError as e: r.fail(f"{e} — this version is not covered"); continue
        st = res[q.entry]
        (r.ok if st == "patched" else r.skip)("patched" if st == "patched" else ("already" if st == "unchanged" else "file not in bundle"))
        if st == "patched": done.append(q.what)
    return done
