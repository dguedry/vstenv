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

import shlex, subprocess
from pathlib import Path

from . import APP_ID, paths, host
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
    log = paths.LOGS / f"url-{s.scheme}.log"
    # The desktop starts the handler with whatever stdio it has: under systemd
    # activation that is a journal socket, and an Electron app that writes to
    # stderr while booting then dies with "open EBADF" (Wine cannot hand a Unix
    # socket to a Windows process as a std handle). Give it a file instead.
    return f'''#!/bin/sh
{MARK}
# {s.title}: the desktop hands {s.scheme}:// links to the program inside this prefix.
[ -n "$1" ] || exit 0
WINEPREFIX={shlex.quote(str(p.path))}
export WINEPREFIX
mkdir -p {shlex.quote(str(log.parent))}
exec {cmd} "$1" </dev/null >>{shlex.quote(str(log))} 2>&1
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
        cp = host.desktop_tool(["xdg-mime", "query", "default", mime(s)], timeout=10)
        default = cp.stdout.strip() if cp else ""
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
        host.desktop_tool(["update-desktop-database", str(APPS)])
        host.desktop_tool(["xdg-mime", "default", desktop.name, mime(s)])
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
    host.desktop_tool(["update-desktop-database", str(APPS)])
    r.ok("removed" if removed else "nothing to remove")
    return removed

class _Program:
    """Stands in for the vendor of a scheme a program registered itself."""
    name = "registered by an installed program"

# Schemes Wine itself registers (winebrowser, its own helpers) are never handed back.
_WINE_SCHEMES = {"http", "https", "ftp", "mailto", "file", "news", "nntp", "snews", "telnet", "ldap",
                 "ms-settings", "ms-windows-store", "res", "about", "javascript"}

def _split_command(cmd: str) -> list[str]:
    """A Windows command line split the way the shell does: quotes group,
    backslashes stay literal (they are path separators here)."""
    out, cur, quoted, started = [], [], False, False
    for ch in cmd:
        if ch == '"': quoted = not quoted; started = True
        elif ch in " \t" and not quoted:
            if started: out.append("".join(cur)); cur, started = [], False
        else: cur.append(ch); started = True
    if started: out.append("".join(cur))
    return out

def _commands(text: str) -> dict[str, str]:
    """{scheme: command} from a hive's '[Software\\Classes\\<scheme>\\shell\\open\\command]'
    default values. hive_sections() drops default (@) values, so this reads them itself."""
    import re
    out, key = {}, None
    for line in text.splitlines():
        if line.startswith("["):
            m = re.match(r'^\[Software\\\\Classes\\\\([^\\\]]+)\\\\shell\\\\open\\\\command\]', line, re.I)
            key = m.group(1) if m else None
        elif key and line.startswith('@="'):
            v = line[3:line.rindex('"')] if line.rstrip().endswith('"') else line[3:]
            out[key.lower()] = v.replace('\\\\', '\\').replace('\\"', '"')
    return out

def program_schemes(p: Prefix, taken: set[str] = frozenset()) -> list[UrlScheme]:
    """URL schemes a program installed in the prefix registered for itself
    ("URL Protocol" under HKLM or HKCU Software\\Classes): a browser sign-in that
    ends at kchub://oauth2... (KORG Software Pass) reaches the program the same
    way a vendor module's declared scheme does. Only commands that run an exe
    inside drive_c, outside Windows' own folder, and pass the URL as the last
    argument; a scheme a vendor module declares is left to it."""
    from .programs import hive_sections
    found: dict[str, UrlScheme] = {}
    for hive, prefix in (("system.reg", "Software\\Classes\\"), ("user.reg", "Software\\Classes\\")):
        path = p.path / hive
        sections = hive_sections(path)
        protocols = {k[len(prefix):].lower() for k, v in sections.items()
                     if k.lower().startswith(prefix.lower()) and "\\" not in k[len(prefix):] and "URL Protocol" in v}
        if not protocols: continue
        try: commands = _commands(path.read_text(encoding="utf-8", errors="replace"))
        except OSError: continue
        for scheme in sorted(protocols):
            if scheme in taken or scheme in _WINE_SCHEMES or scheme in found: continue
            if not all(c.isalnum() or c in "+.-" for c in scheme): continue
            argv = _split_command(commands.get(scheme, ""))
            if len(argv) < 2 or argv[-1] != "%1" or "%1" in argv[:-1]: continue
            exe = argv[0]
            if not exe.lower().startswith("c:\\") or exe.lower().startswith("c:\\windows\\"): continue
            host_exe = p.to_host(exe)
            if not host_exe.is_file(): continue
            extra = argv[1:-1]
            found[scheme] = UrlScheme(scheme, f"{host_exe.stem} link handler",
                                      lambda p, e=host_exe, x=tuple(extra): [str(p.build.wine), str(e), *x])
    return list(found.values())

def all_schemes(p: Prefix) -> list[tuple]:
    from . import vendors
    declared = [(v, s) for v in vendors.all() for s in v.url_schemes(p)]
    taken = {s.scheme.lower() for _v, s in declared}
    try: discovered = [(_Program, s) for s in program_schemes(p, taken)]
    except Exception: discovered = []
    return declared + discovered

def register_all(p: Prefix, reporter=None) -> bool:
    ok = True
    for _, s in all_schemes(p):
        try: ok = register(p, s, reporter) and ok
        except Exception as e:
            r = null_reporter(reporter); r.step(f"{s.scheme}:// handler"); r.fail(str(e)[:80]); ok = False
    return ok
