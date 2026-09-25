"""Other vstenv prefixes on this machine, and who owns a vendor daemon's ports.

A machine can end up with several vstenv prefixes: a source install
(~/.local/share/vstenv) and the Flatpak (~/.var/app/<id>/data/vstenv). Each has
its own wineserver, registry and vendor daemons; a daemon that binds fixed
localhost ports (NI's NTK daemon) can only be owned by one. A plugin bridged
from prefix A while prefix B's daemon holds the ports talks to the wrong daemon
and hangs in every DAW. The rules that keep this consistent:

- the vstenv that runs owns the bridges: sync() removes other vstenv
  prefixes' directories from yabridgectl (third-party prefixes are left alone)
- Health names the prefix whose daemon holds the ports, by reading the
  listener's WINEPREFIX, instead of guessing from "our wineserver is up"
- the Flatpak sandbox must see every path Wine reaches through the prefix:
  wineserver opens files for every client, and whichever side started it
  (the app in its sandbox, or a DAW's yabridge on the host) serves the other.
  Wine links the prefix's Documents/Music/... to the host home; a sandbox
  without that view makes plugins abort at load in every DAW. sandbox_gaps()
  lists what the sandbox cannot see.
"""
import os, re, socket
from pathlib import Path
from .wine import Prefix
from . import host

PREFIX_RE = re.compile(r"^(.*/vstenv/prefix)(?:/drive_c(?:/.*)?)?/?$")

def app_prefixes() -> list[Path]:
    """Every existing vstenv prefix: source install, every Flatpak app id, and
    whatever XDG_DATA_HOME points at."""
    from . import paths
    home = Path.home()
    cands = [paths.PREFIX, home / ".local/share/vstenv/prefix"]
    var = home / ".var/app"
    if var.is_dir():
        cands += sorted(var.glob("*/data/vstenv/prefix"))
    out: list[Path] = []
    for c in cands:
        if (c / "drive_c" / "windows").exists() and not any(_same(c, o) for o in out):
            out.append(c)
    return out

def _same(a: Path, b: Path) -> bool:
    try: return a.resolve() == b.resolve()
    except OSError: return a == b

def other_prefixes(p: Prefix) -> list[Path]:
    return [x for x in app_prefixes() if not _same(x, p.path)]

def prefix_of_dir(d: str | Path) -> Path | None:
    """The vstenv prefix a directory lives in, or None (third-party prefix, host dir)."""
    try: s = str(Path(d).resolve())
    except OSError: s = str(d)
    m = PREFIX_RE.match(s.rstrip("/"))
    return Path(m.group(1)) if m else None

def yabridgectl_dirs(status_text: str) -> list[str]:
    """Plugin directories from `yabridgectl status` output (the unindented lines)."""
    return [l.strip() for l in status_text.splitlines() if l.startswith("/") and l.rstrip().endswith("/")]

def foreign_yabridge_dirs(p: Prefix, status_text: str) -> list[str]:
    """Registered directories that belong to a *different* vstenv prefix."""
    out = []
    for d in yabridgectl_dirs(status_text):
        owner = prefix_of_dir(d)
        if owner is not None and not _same(owner, p.path): out.append(d)
    return out

def short(path: str | Path) -> str:
    s = str(path)
    home = str(Path.home())
    return "~" + s[len(home):] if s.startswith(home) else s

# --- processes ---------------------------------------------------------------------------------
def _environ(pid: int) -> dict[str, str]:
    try: raw = (Path("/proc") / str(pid) / "environ").read_bytes()
    except OSError: return {}
    out = {}
    for item in raw.split(b"\0"):
        k, _, v = item.partition(b"=")
        if k: out[k.decode(errors="replace")] = v.decode(errors="replace")
    return out

def _comm(pid: int) -> str:
    try: return (Path("/proc") / str(pid) / "comm").read_text().strip()
    except OSError: return ""

def _cmdline(pid: int) -> str:
    try: return (Path("/proc") / str(pid) / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace").strip()
    except OSError: return ""

def _listeners(ports: tuple[int, ...]) -> dict[int, int]:
    """port -> socket inode for LISTEN sockets on 127.0.0.1 (from /proc/net/tcp)."""
    out: dict[int, int] = {}
    try: lines = Path("/proc/net/tcp").read_text().splitlines()[1:]
    except OSError: return out
    for l in lines:
        f = l.split()
        if len(f) < 10 or f[3] != "0A": continue        # 0A = LISTEN
        addr, port_hex = f[1].split(":")
        if addr != "0100007F": continue                 # 127.0.0.1, little-endian hex
        port = int(port_hex, 16)
        if port in ports: out[port] = int(f[9])
    return out

_HOLDER_SCAN = r"""for p in /proc/[0-9]*; do
  for fd in "$p"/fd/*; do
    [ "$(readlink "$fd" 2>/dev/null)" = "socket:[$VSTENV_INODE]" ] || continue
    printf '%s\t%s\t%s\t' "${p#/proc/}" "$(cat "$p/comm" 2>/dev/null)" "$(tr '\0' '\n' < "$p/environ" 2>/dev/null | grep '^WINEPREFIX=' | head -1 | cut -d= -f2-)"
    tr '\0' ' ' < "$p/cmdline" 2>/dev/null; echo; break
  done
done"""

def _socket_holders(inode: int) -> list[dict]:
    """Processes holding this socket, host-wide: [{'pid', 'comm', 'prefix', 'cmd'}]."""
    out = []
    for line in host.sh(_HOLDER_SCAN, env={"VSTENV_INODE": str(inode)}, timeout=120).splitlines():
        parts = line.split("\t", 3)
        if len(parts) == 4 and parts[0].isdigit():
            out.append({"pid": int(parts[0]), "comm": parts[1], "prefix": parts[2], "cmd": parts[3].strip()})
    return out

def _pid_for_inode(inode: int) -> dict | None:
    """A process holding this socket. Wine sockets are shared by wineserver and the
    Windows process that owns them; prefer the latter for a meaningful name."""
    holders = _socket_holders(inode)
    if not holders: return None
    named = [h for h in holders if "wineserver" not in h["cmd"]]
    return (named or holders)[0]

def port_owner(ports: tuple[int, ...]) -> dict:
    """Who listens on a vendor daemon's ports.
    {'busy': [ports], 'pid': int|None, 'prefix': str|None, 'exe': str}. pid/prefix are
    None when a port is busy but its holder is invisible (another pid namespace)."""
    inodes = _listeners(ports)
    busy = sorted(inodes)
    if not busy:
        # /proc/net/tcp is per network namespace; double-check with a bind attempt
        for port in ports:
            with socket.socket() as sk:
                try: sk.bind(("127.0.0.1", port))
                except OSError: busy.append(port)
        return {"busy": busy, "pid": None, "prefix": None, "exe": ""}
    h = None
    for port in busy:
        h = _pid_for_inode(inodes[port])
        if h is not None: break
    if h is None: return {"busy": busy, "pid": None, "prefix": None, "exe": ""}
    return {"busy": busy, "pid": h["pid"], "prefix": h["prefix"] or None, "exe": h["comm"]}

_WINESERVER_SCAN = r"""for p in /proc/[0-9]*; do
  [ "$(cat "$p/comm" 2>/dev/null)" = wineserver ] || continue
  printf '%s\t%s\n' "${p#/proc/}" "$(tr '\0' '\n' < "$p/environ" 2>/dev/null | grep '^WINEPREFIX=' | head -1 | cut -d= -f2-)"
done"""

def wineservers() -> list[dict]:
    """Running wineservers, host-wide: [{'pid', 'prefix'}]."""
    out = []
    for line in host.sh(_WINESERVER_SCAN).splitlines():
        pid, _, prefix = line.partition("\t")
        if pid.isdigit(): out.append({"pid": int(pid), "prefix": prefix.strip()})
    return out

def report(p: Prefix, ports: tuple[int, ...], status_text: str = "") -> str:
    """Human-readable overview for `vstenv prefixes`."""
    lines = [f"this prefix:   {short(p.path)}"]
    others = other_prefixes(p)
    lines.append("other prefixes: " + (", ".join(short(o) for o in others) if others else "none"))
    ws = wineservers()
    lines.append("wineservers:    " + (", ".join(f"{short(w['prefix']) or '?'} (pid {w['pid']})" for w in ws) if ws else "none running"))
    if ports:
        o = port_owner(ports)
        if not o["busy"]: lines.append("vendor daemon:  not running (starts with the vendor's manager / the first plugin)")
        elif o["prefix"]: lines.append(f"vendor daemon:  {o['exe']} pid {o['pid']} in {short(o['prefix'])}" + ("" if _same(Path(o["prefix"]), p.path) else "   <-- ANOTHER PREFIX"))
        else: lines.append(f"vendor daemon:  ports {o['busy']} held by a process this sandbox cannot see")
    if status_text:
        foreign = foreign_yabridge_dirs(p, status_text)
        lines.append("yabridgectl:    " + (f"{len(foreign)} directories of other vstenv prefixes (vstenv sync removes them)" if foreign else "only this prefix's directories"))
    return "\n".join(lines)


# --- Flatpak sandbox view ------------------------------------------------------------
FLATPAK_INFO = Path("/.flatpak-info")
XDG_DIRS = {"xdg-desktop": "Desktop", "xdg-documents": "Documents", "xdg-download": "Downloads",
            "xdg-music": "Music", "xdg-pictures": "Pictures", "xdg-videos": "Videos",
            "xdg-templates": "Templates", "xdg-publicshare": "Public"}

def flatpak_filesystems(info: Path = FLATPAK_INFO) -> list[str] | None:
    """The sandbox's ``filesystems=`` grants from /.flatpak-info, or None when not
    running under Flatpak."""
    if not info.exists(): return None
    for line in info.read_text(errors="ignore").splitlines():
        if line.startswith("filesystems="):
            return [x for x in line[len("filesystems="):].split(";") if x]
    return []

def _under(path: Path, root: Path) -> bool:
    return path == root or root in path.parents

def sandbox_sees(path: Path, filesystems: list[str], home: Path | None = None) -> bool:
    """Whether a host path is visible inside a sandbox with these grants. The app's
    own data dir (~/.var/app/<id>) is always mounted."""
    home = home or Path.home(); path = Path(path)
    if _under(path, home / ".var" / "app"): return True
    for fs in filesystems:
        name = fs.split(":")[0]
        if name == "host": return True
        if name == "home":
            if _under(path, home): return True
            continue
        if name in XDG_DIRS: root = home / XDG_DIRS[name]
        elif name.startswith("xdg-run/"): root = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")) / name[len("xdg-run/"):]
        elif name.startswith("~/"): root = home / name[2:]
        elif name.startswith("/"): root = Path(name)
        else: continue
        if _under(path, root): return True
    return False

def _unix_path(p: Prefix, win: str) -> Path | None:
    """C:\\... -> drive_c/..., Z:\\... -> /...; other drive letters are unknown here."""
    if len(win) < 3 or win[1:3] != ":\\": return None
    rest = win[3:].replace("\\", "/")
    if win[0].upper() == "C": return p.drive_c / rest
    if win[0].upper() == "Z": return Path("/") / rest
    return None

def host_paths_wine_opens(p: Prefix) -> dict[str, Path]:
    """Places outside the prefix that Wine reaches through it: the user folders Wine
    symlinks to the host home (Desktop, Documents, Music, ...) and library
    ContentDirs on other drives. Keys are Windows paths, values host paths."""
    out: dict[str, Path] = {}
    users = p.user_dir
    if users.exists():
        for entry in sorted(users.iterdir()):
            if entry.is_symlink():
                out[f"C:\\users\\{users.name}\\{entry.name}"] = Path(os.path.realpath(entry))
    from . import vendors
    for v in vendors.all():
        for win in v.content_dirs(p):
            u = _unix_path(p, win)
            if u is not None and not _under(u, p.path):
                out[win] = Path(os.path.realpath(u))
    return out

def sandbox_gaps(p: Prefix, filesystems: list[str] | None = None, home: Path | None = None) -> list[str]:
    """Paths Wine reaches through this prefix that the Flatpak sandbox cannot see;
    empty outside Flatpak. A wineserver started from such a sandbox fails every
    client's open on them with "no such file": plugins abort at load in every DAW."""
    if filesystems is None: filesystems = flatpak_filesystems()
    if filesystems is None: return []
    return [f"{win} -> {unix}" for win, unix in host_paths_wine_opens(p).items()
            if not sandbox_sees(unix, filesystems, home)]
