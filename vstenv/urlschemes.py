"""URL schemes a program in the prefix must receive from the desktop.

A vendor's manager may finish a browser sign-in by redirecting to its own
scheme (native-access://...). Wine registers the scheme inside the prefix, but
nothing registers it with the Linux desktop, so the browser has nowhere to send
the callback and the login never completes. For every UrlScheme a vendor module
declares, this installs a tiny handler on the host: a .desktop file claiming the
scheme and a script that runs the vendor's command with the URL. Both live under
~/.local, need no root, and are removed by unregister().
"""
from __future__ import annotations

import shlex, shutil, subprocess
from pathlib import Path

from . import APP_ID
from .progress import null_reporter
from .vendors import UrlScheme
from .wine import Prefix

APPS = Path.home() / ".local/share/applications"
BIN = Path.home() / ".local/bin"
MARK = "# vstenv url handler"

def script_path(s: UrlScheme) -> Path: return BIN / f"vstenv-url-{s.scheme}"
def desktop_path(s: UrlScheme) -> Path: return APPS / f"{APP_ID}.url-{s.scheme}.desktop"
def mime(s: UrlScheme) -> str: return f"x-scheme-handler/{s.scheme}"

def script_content(p: Prefix, s: UrlScheme) -> str:
    """The handler runs on the host (the .desktop file is a host file), so it calls
    this prefix's wine directly rather than going back through the app."""
    cmd = " ".join(shlex.quote(a) for a in s.argv(p))
    return f'''#!/bin/sh
{MARK}
# {s.title}: the desktop hands {s.scheme}:// links to the program inside this prefix.
[ -n "$1" ] || exit 0
WINEPREFIX={shlex.quote(str(p.path))}
export WINEPREFIX
exec {cmd} "$1"
'''

def desktop_content(s: UrlScheme) -> str:
    return f"""[Desktop Entry]
Type=Application
Name={s.title}
Comment=Passes {s.scheme}:// links into the vstenv prefix
Exec={script_path(s)} %u
NoDisplay=true
Terminal=false
MimeType={mime(s)};
"""

def _ours(path: Path) -> bool:
    try: return MARK in path.read_text(errors="replace")
    except OSError: return False

def status(s: UrlScheme) -> dict:
    """Is the handler installed, and is the desktop actually using it?"""
    default = ""
    try:
        default = subprocess.run(["xdg-mime", "query", "default", mime(s)], capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError): pass
    installed = script_path(s).exists() and desktop_path(s).exists() and _ours(script_path(s))
    return {"installed": installed, "default": default, "is_default": default == desktop_path(s).name,
            "ok": installed and default == desktop_path(s).name,
            "foreign": bool(default) and default != desktop_path(s).name}

def register(p: Prefix, s: UrlScheme, reporter=None) -> bool:
    r = null_reporter(reporter)
    r.step(f"{s.title} ({s.scheme}:// handler)")
    st = status(s)
    if st["foreign"]:
        # Someone else claims the scheme -- very likely the user's own workaround.
        # Replacing it is the point, but say so rather than doing it silently.
        r.log(f"replacing the existing handler for {s.scheme}:// ({st['default']})")
    script, desktop = script_path(s), desktop_path(s)
    try:
        BIN.mkdir(parents=True, exist_ok=True); APPS.mkdir(parents=True, exist_ok=True)
        want = script_content(p, s)
        if not script.exists() or script.read_text(errors="replace") != want: script.write_text(want)
        script.chmod(0o755)
        if not desktop.exists() or desktop.read_text(errors="replace") != desktop_content(s): desktop.write_text(desktop_content(s))
        if shutil.which("update-desktop-database"):
            subprocess.run(["update-desktop-database", str(APPS)], capture_output=True, timeout=30)
        if shutil.which("xdg-mime"):
            subprocess.run(["xdg-mime", "default", desktop.name, mime(s)], capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as e:
        r.fail(str(e)[:100]); return False
    after = status(s)
    if after["ok"]: r.ok("registered")
    elif after["installed"]: r.ok("installed (the desktop still lists another handler)")
    else: r.fail("could not register")
    return after["installed"]

def unregister(s: UrlScheme, reporter=None) -> bool:
    r = null_reporter(reporter)
    r.step(f"Removing the {s.scheme}:// handler")
    removed = False
    for f in (script_path(s), desktop_path(s)):
        if f.exists() and (f.suffix == ".desktop" or _ours(f)):
            try: f.unlink(); removed = True
            except OSError as e: r.fail(str(e)[:80]); return False
    if shutil.which("update-desktop-database"):
        subprocess.run(["update-desktop-database", str(APPS)], capture_output=True, timeout=30)
    r.ok("removed" if removed else "nothing to remove")
    return removed

def all_schemes(p: Prefix) -> list[tuple]:
    from . import vendors
    return [(v, s) for v in vendors.all() for s in v.url_schemes(p)]

def register_all(p: Prefix, reporter=None) -> bool:
    ok = True
    for _, s in all_schemes(p):
        try: ok = register(p, s, reporter) and ok
        except Exception as e:
            r = null_reporter(reporter); r.step(f"{s.scheme}:// handler"); r.fail(str(e)[:80]); ok = False
    return ok
