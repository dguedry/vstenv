"""Keep $XDG_RUNTIME_DIR/wine private, as other Wine builds on the host demand.

Debian's and Ubuntu's Wine packages put wineserver's sockets in
/run/user/<uid>/wine instead of /tmp/.wine-<uid>, and refuse to start when that
folder is readable by anyone else ("wineserver: /run/user/1000/wine must not be
accessible by other users"). Every Wine program using that build then fails,
and a DAW scanning plugins through it shows nothing but a crash on scan.

Older vstenv Flatpaks granted `xdg-run/wine:create`, and Flatpak creates a
missing granted folder on the host with mode 0755 at every launch, so having
vstenv installed broke the distro's Wine until the next reboot. The grant is
gone (vstenv's own Wine uses /tmp/.wine-<uid> only), but the folder it left
behind stays until /run/user is cleared. It is ours and Wine itself creates it
0700, so setting 0700 is exactly what Wine would have done.

The folder is on the host: the sandbox's /run/user is its own, so everything
here runs through host.sh.
"""
from __future__ import annotations

from . import host

FOLDER = '"${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/wine"'

def status() -> dict:
    """{'path', 'mode', 'ours', 'ok'}; ok when the folder is absent or private."""
    out = host.sh(f'd={FOLDER}; [ -d "$d" ] && [ ! -L "$d" ] || {{ echo "absent $d"; exit 0; }}; '
                  f'[ -O "$d" ] && o=ours || o=other; echo "$(stat -c %a "$d") $o $d"').strip()
    if not out: return {"path": "", "mode": "", "ours": True, "ok": True}       # host not reachable: nothing to say
    mode, rest = out.split(" ", 1)
    if mode == "absent": return {"path": rest, "mode": "", "ours": True, "ok": True}
    ours, path = rest.split(" ", 1)
    return {"path": path, "mode": mode, "ours": ours == "ours", "ok": ours == "ours" and int(mode, 8) & 0o077 == 0}

def heal() -> bool:
    """Make the folder 0700 when it is ours and open to others. True if changed."""
    s = status()
    if s["ok"] or not s["ours"]: return False
    host.sh(f'chmod 700 {FOLDER}')
    return status()["ok"]
