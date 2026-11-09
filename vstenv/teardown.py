"""Remove the environment: everything setup made, so the next start sets up fresh.

Deleting ~/.local/share/vstenv by hand, or uninstalling the Flatpak (which keeps
it: Flatpak only deletes ~/.var/app), leaves pieces a fresh start then trips
over: the DAW still lists bridged plugins whose prefix is gone, and a prefix that
survived means Native Access finds the old products and offers "Repair". This
takes away, in order:

1. every Wine process in the prefix (wineserver -k);
2. the prefix's plugin folders in yabridgectl, and with them the bridged copies in
   ~/.vst, ~/.vst3 and ~/.clap (yabridgectl sync --prune);
3. the menu entries, the file manager entries, the vendors' URL handlers, and the
   Cinnamon setting setup changed (only if it is still the one setup set);
4. ~/.local/share/vstenv and ~/.config/vstenv: the prefix with every installed
   program, plugin, library and login, the app's Wine, its patched DLLs, logs
   and settings;
5. with downloads=True, the cached downloads in ~/.cache/vstenv too.

Left in place: yabridge itself (~/.local/share/yabridge; other setups may use it
and setup re-uses it), licences held on vendor accounts, and the app.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from . import paths
from .progress import null_reporter


def _under(path: str | Path, root: Path) -> bool:
    try: Path(path).resolve().relative_to(root.resolve()); return True
    except (ValueError, OSError): return False

def unbridge(p, reporter=None) -> int:
    """Take the prefix's plugin folders out of yabridgectl and prune their bridges."""
    from . import yabridge, prefixes
    r = null_reporter(reporter); r.step("Removing the bridged plugins from your DAWs' folders")
    if not yabridge.YCTL.exists(): r.skip("yabridge is not installed"); return 0
    env = yabridge._yctl_env(p); n = 0
    for d in prefixes.yabridgectl_dirs(yabridge.status(p, fresh=True)):
        if _under(d, p.path):
            if subprocess.run([str(yabridge.YCTL), "rm", d], capture_output=True, env=env).returncode == 0: n += 1
    cp = subprocess.run([str(yabridge.YCTL), "sync", "--prune"], capture_output=True, text=True, env=env, timeout=1800)
    yabridge.forget_status(p)
    (r.ok if cp.returncode == 0 else r.fail)(f"{n} plugin folder(s) unregistered")
    return n

def remove_environment(p, reporter=None, downloads: bool = False) -> None:
    """Remove everything setup made (see the module docstring). `p` may be None
    when there is no usable prefix; the files still go."""
    from . import menu, filemanager, urlschemes, desktopfix
    r = null_reporter(reporter)
    if p is not None:
        r.step("Stopping Wine in the prefix")
        try: p.kill(); r.ok()
        except Exception as e: r.skip(str(e)[:80])
        try: unbridge(p, r)
        except Exception as e: r.step("Removing the bridged plugins"); r.fail(str(e)[:100])
        try:
            for _v, s in urlschemes.all_schemes(p): urlschemes.unregister(s, r)
        except Exception as e: r.step("Removing URL handlers"); r.fail(str(e)[:100])
    for step in (menu.remove_all, filemanager.remove, desktopfix.reset):
        try: step(r)
        except Exception as e: r.step(getattr(step, "__name__", "cleanup")); r.fail(str(e)[:100])
    targets = [paths.DATA, paths.CONFIG] + ([paths.CACHE] if downloads else [])
    for d in targets:
        r.step(f"Deleting {d}")
        if not d.exists(): r.skip("not there"); continue
        shutil.rmtree(d, ignore_errors=True)
        (r.fail if d.exists() else r.ok)("some files could not be deleted" if d.exists() else "")
