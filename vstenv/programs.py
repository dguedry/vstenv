"""Any Windows program in the prefix: list, run, install, uninstall.

Wine's menu builder is disabled in this prefix (no .desktop spam), so an
installed program is invisible once its installer closes. Two sources say what
is installed, both readable without starting Wine:

- the Uninstall keys installers write (HKLM, its Wow6432Node view, HKCU), parsed
  straight from the prefix's system.reg / user.reg: name, version, publisher,
  install location, icon exe, uninstall command;
- Start Menu shortcuts (.lnk, Shell Link binary format): target exe, arguments,
  working directory. Wine writes them with a LinkInfo block, which is where the
  target path lives.

Registry entries give the uninstaller; shortcuts give the launcher; the two are
merged by name. The prefix is tuned for audio software: Windows 10, a real C
runtime, Direct3D through DXVK, and .NET through Wine Mono when something
needs it (a vendor module asks for it, or the user installs it from the
Install tab). No embedded browser (Gecko) is installed: nothing supported
needs one. Vendor modules contribute extra program sources (Vendor.programs).
"""
import os, re, shlex, struct, threading, time
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath
from .progress import null_reporter
from .wine import Prefix
from . import quirks

UNINSTALL_KEYS = (r"Software\Microsoft\Windows\CurrentVersion\Uninstall",
                  r"Software\Wow6432Node\Microsoft\Windows\CurrentVersion\Uninstall")
SKIP_NAMES = re.compile(r"^(Wine (Mono|Gecko)|Microsoft Visual C\+\+|Windows .*Runtime)", re.I)
NOT_A_LAUNCHER = re.compile(r"(unins|uninstall|setup|update|crash|helper)", re.I)
LIMITS = ("Windows installers and programs run with the environment's own Wine. Direct3D goes through DXVK; "
          ".NET is available once Wine Mono is installed (below); programs that need an embedded browser (Gecko) will not run.")

@dataclass
class Program:
    name: str
    version: str = ""
    publisher: str = ""
    exe: str = ""                 # Windows path of the launcher, "" if none known
    args: str = ""
    workdir: str = ""             # Windows path
    install_dir: str = ""         # Windows path
    uninstall: str = ""           # UninstallString as written by the installer
    sources: list = field(default_factory=list)   # "registry", "shortcut", "vendor", a vendor id
    @property
    def runnable(self) -> bool: return bool(self.exe)
    @property
    def vendor(self) -> str:
        """The vendor module this program belongs to ("" for none)."""
        from . import vendors
        v = vendors.for_program(self); return v.id if v else ""

# --- Wine .reg files --------------------------------------------------------------------------
def _unescape(s: str) -> str:
    out, i = [], 0
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s): out.append(s[i + 1]); i += 2
        else: out.append(c); i += 1
    return "".join(out)

def reg_sections(text: str) -> dict[str, dict[str, str]]:
    """{'Key\\Path': {'Name': 'string value'}} for a Wine .reg file. Only string
    values (plain and str(2):) and dwords (as decimal strings) are kept."""
    out: dict[str, dict[str, str]] = {}; cur = None
    for line in text.splitlines():
        if line.startswith("["):
            key = line[1:line.rindex("]")] if "]" in line else line[1:]
            cur = out.setdefault(_unescape(key), {}); continue
        if cur is None or not line.startswith('"'): continue
        m = re.match(r'^"((?:[^"\\]|\\.)*)"=(.*)$', line)
        if not m: continue
        name, val = _unescape(m.group(1)), m.group(2)
        if val.startswith('"') and val.endswith('"'): cur[name] = _unescape(val[1:-1])
        elif val.startswith('str(2):"') and val.endswith('"'): cur[name] = _unescape(val[8:-1])
        elif val.startswith("dword:"): cur[name] = str(int(val[6:], 16))
    return out

def registry_programs(p: Prefix) -> list[Program]:
    out = []
    for regfile in (p.path / "system.reg", p.path / "user.reg"):
        try: sections = reg_sections(regfile.read_text(encoding="utf-8", errors="replace"))
        except OSError: continue
        for key, vals in sections.items():
            if not any(key.lower().startswith(u.lower() + "\\") for u in UNINSTALL_KEYS): continue
            name = vals.get("DisplayName", "").strip()
            if not name or SKIP_NAMES.match(name) or vals.get("SystemComponent") == "1": continue
            icon = vals.get("DisplayIcon", "").split(",")[0].strip().strip('"')
            launcher = icon if icon.lower().endswith(".exe") and not NOT_A_LAUNCHER.search(Path(icon).name) else ""
            out.append(Program(name=name, version=vals.get("DisplayVersion", ""), publisher=vals.get("Publisher", ""),
                               exe=launcher, install_dir=vals.get("InstallLocation", ""),
                               uninstall=vals.get("UninstallString", ""), sources=["registry"]))
    return out

# --- Shell Link (.lnk) -------------------------------------------------------------------------
def parse_lnk(data: bytes) -> dict:
    """{'target', 'args', 'workdir', 'name'} from a Shell Link. Target comes from the
    LinkInfo block (LocalBasePath + CommonPathSuffix); missing pieces are ''."""
    res = {"target": "", "args": "", "workdir": "", "name": ""}
    if len(data) < 0x4C or data[:4] != b"L\0\0\0": return res
    flags = struct.unpack_from("<I", data, 0x14)[0]
    pos = 0x4C
    if flags & 0x01:                                            # HasLinkTargetIDList
        pos += 2 + struct.unpack_from("<H", data, pos)[0]
    if flags & 0x02:                                            # HasLinkInfo
        base = pos
        size, hsize, iflags, _vol, lbp_off, _cnrl, cps_off = struct.unpack_from("<7I", data, base)
        def cstr(off):
            if not off: return ""
            end = data.index(b"\0", base + off); return data[base + off:end].decode("cp1252", errors="replace")
        if iflags & 0x01:                                       # VolumeIDAndLocalBasePath
            res["target"] = cstr(lbp_off) + cstr(cps_off)
        pos = base + size
    unicode = bool(flags & 0x80)
    def sdata():
        nonlocal pos
        n = struct.unpack_from("<H", data, pos)[0]; pos += 2
        if unicode: s = data[pos:pos + 2 * n].decode("utf-16-le", errors="replace"); pos += 2 * n
        else: s = data[pos:pos + n].decode("cp1252", errors="replace"); pos += n
        return s.strip("\0")        # Wine counts the terminating NUL in the length
    try:
        if flags & 0x04: res["name"] = sdata()                  # HasName
        if flags & 0x08: rel = sdata(); res["target"] = res["target"] or rel   # HasRelativePath
        if flags & 0x10: res["workdir"] = sdata()               # HasWorkingDir
        if flags & 0x20: res["args"] = sdata()                  # HasArguments
    except (struct.error, IndexError): pass
    return res

NOT_A_LAUNCHER_NAME = re.compile(r"(unins|uninstall|setup|update)", re.I)   # shortcut names ("Uninstall", "… Setup (x64)")
UNINSTALL_ARGS = re.compile(r"(^|\s)(/x|/uninstall|--uninstall)(\s|$)", re.I)  # msiexec /x {guid}, and friends

def long_paths(p: Prefix, paths_: list[str]) -> dict[str, str]:
    """Wine's 8.3 short names (C:\\PROG~FBU\\…) resolved to long paths, in one
    winepath call; a path that cannot be resolved maps to itself. MSI-installed
    shortcuts carry such targets, which nothing outside Wine can open."""
    short = [x for x in dict.fromkeys(paths_) if "~" in x]
    if not short: return {}
    key = (str(p.path), tuple(short))
    if key in _long_paths_cache: return _long_paths_cache[key]      # the GUI lists programs often; 8.3 names do not move
    try:
        cp = p.run(["winepath", "-l", *short], timeout=120)
        out = cp.stdout.splitlines()
    except Exception: return {}
    res = {s: (l.strip() or s) for s, l in zip(short, out)} if len(out) >= len(short) else {}
    if res: _long_paths_cache[key] = res
    return res
_long_paths_cache: dict[tuple, dict[str, str]] = {}

def shortcut_programs(p: Prefix) -> list[Program]:
    roots = [p.drive_c / "ProgramData/Microsoft/Windows/Start Menu/Programs",
             p.user_dir / "AppData/Roaming/Microsoft/Windows/Start Menu/Programs",
             p.drive_c / "users/Public/Desktop", p.user_dir / "Desktop"]
    found = []
    for root in roots:
        if not root.is_dir(): continue
        for lnk in sorted(root.rglob("*.lnk")):
            try: info = parse_lnk(lnk.read_bytes())
            except OSError: continue
            target = info["target"]
            if not target.lower().endswith(".exe"): continue
            if NOT_A_LAUNCHER_NAME.search(lnk.stem) or NOT_A_LAUNCHER.search(Path(target).name) or UNINSTALL_ARGS.search(info["args"] or ""): continue
            found.append((lnk, info))
    resolved = long_paths(p, [info["target"] for _, info in found] + [info["workdir"] for _, info in found if info["workdir"]])
    out, seen = [], set()
    for lnk, info in found:
        target = resolved.get(info["target"], info["target"]); workdir = resolved.get(info["workdir"], info["workdir"])
        low = target.lower()
        if low in seen: continue
        seen.add(low)
        parent = _win_parent(target)
        out.append(Program(name=lnk.stem, exe=target, args=info["args"], workdir=workdir or parent, install_dir=parent, sources=["shortcut"]))
    return out

def _win_parent(win_path: str) -> str:
    """Directory of a Windows path (pathlib on Linux would treat backslashes as part of the name)."""
    return str(PureWindowsPath(win_path).parent)

# --- merge -----------------------------------------------------------------------------------
def _norm(s: str) -> str: return re.sub(r"[^a-z0-9]", "", s.lower())

def _bare(s: str) -> str:
    """Name without a leading vendor name ("Native Instruments Dirt" -> "dirt"), normalised."""
    from . import vendors
    n = _norm(s)
    for v in vendors.all():
        vn = _norm(v.name)
        if vn and n.startswith(vn) and len(n) > len(vn): return n[len(vn):]
    return n

def find_exe(p: Prefix, install_dir: str) -> str:
    """The most plausible launcher in an install directory: the largest .exe that
    is not an uninstaller or setup helper."""
    if not install_dir: return ""
    d = p.to_host(install_dir)
    if not d.is_dir(): return ""
    cands = [e for e in d.glob("*.exe") if not NOT_A_LAUNCHER.search(e.name)]
    if not cands: return ""
    best = max(cands, key=lambda e: e.stat().st_size)
    return install_dir.rstrip("\\") + "\\" + best.name

# One refresh of the GUI asks for this list several times: the Plugins page, the
# Programs page, and dcomp.program_exes() which walks it to read import tables.
# Each build reads the registry and the Start Menu (about two seconds), so the
# result is held for a few seconds -- long enough for one pass to reuse it,
# short enough that an install finishing is still picked up by the refresh that
# follows it. invalidate() drops it after anything that changes the prefix.
_CACHE: dict = {}
_CACHE_TTL = 8.0

def invalidate():
    """Forget the cached program list (after an install, uninstall or repair)."""
    _CACHE.clear()

def installed(p: Prefix, fresh: bool = False) -> list[Program]:
    """Programs in the prefix, merged from the registry and Start Menu shortcuts.

    Cached briefly; pass fresh=True to force a re-read."""
    import time
    key = str(p.path)
    hit = _CACHE.get(key)
    if not fresh and hit is not None and time.monotonic() - hit[0] < _CACHE_TTL:
        return hit[1]
    out = _installed_uncached(p)
    _CACHE[key] = (time.monotonic(), out)
    return out

def _installed_uncached(p: Prefix) -> list[Program]:
    from . import vendors
    regs = registry_programs(p) + [x for v in vendors.all() for x in v.programs(p)]; links = shortcut_programs(p)
    by_name: dict[str, Program] = {}
    for r in regs:
        k = _norm(r.name)
        if k in by_name:                                         # HKLM + Wow6432Node duplicates: keep the fuller one
            cur = by_name[k]
            for f in ("version", "publisher", "exe", "install_dir", "uninstall"):
                if not getattr(cur, f): setattr(cur, f, getattr(r, f))
            for s in r.sources:
                if s not in cur.sources: cur.sources.append(s)
        else: by_name[k] = r
    def _p(x):                       # BitRock and friends write registry paths with
        return (x or "").lower().replace("/", "\\")   # forward slashes; compare normalized
    for l in links:
        match = None
        for r in by_name.values():
            if _norm(l.name) == _norm(r.name) or _bare(l.name) == _bare(r.name) \
           or (r.exe and _p(l.exe) == _p(r.exe)) \
           or (r.install_dir and _p(l.exe).startswith(_p(r.install_dir).rstrip("\\") + "\\")):
                match = r; break
        if match:
            match.exe, match.args, match.workdir = l.exe, l.args, l.workdir
            if "shortcut" not in match.sources: match.sources.append("shortcut")
        else: by_name[_norm(l.name)] = l
    for prog in by_name.values():
        if not prog.exe: prog.exe = find_exe(p, prog.install_dir)
        if prog.exe and not prog.workdir: prog.workdir = _win_parent(prog.exe)
        if prog.exe and not prog.install_dir: prog.install_dir = prog.exe.rsplit("\\", 1)[0]   # registry records without InstallLocation
    return sorted(by_name.values(), key=lambda x: x.name.lower())

def find(p: Prefix, name: str) -> Program:
    progs = installed(p)
    exact = [x for x in progs if x.name.lower() == name.lower()]
    if exact: return exact[0]
    part = [x for x in progs if name.lower() in x.name.lower()]
    if len(part) == 1: return part[0]
    if not part: raise LookupError(f"no installed program matches '{name}' (vstenv programs lists them)")
    raise LookupError(f"'{name}' is ambiguous: " + ", ".join(x.name for x in part))

# --- actions ---------------------------------------------------------------------------------
def run(p: Prefix, prog: Program, reporter=None):
    """Start the program detached; returns the Popen (ends when the program exits)."""
    r = null_reporter(reporter)
    from . import vendors
    v = vendors.for_program(prog)
    if v is not None and "vendor" in prog.sources and v.is_manager_program(prog):
        return v.launch_manager(p, r)          # the vendor's manager has its own launch fixes
    why = v.cannot_run(p, prog) if v is not None else None
    if why: raise RuntimeError(f"{prog.name} cannot run here: {why}")
    if not prog.exe: raise RuntimeError(f"{prog.name} has no known launcher (only an uninstaller)")
    if prog.install_dir: quirks.apply(p, prog.name, prog.install_dir, r)
    r.step(f"Starting {prog.name}")
    cwd = p.to_host(prog.workdir) if prog.workdir else None
    extra = quirks.launch_args(p, prog.name, prog.install_dir) if prog.install_dir else []
    if extra: r.log(f"launch arguments: {' '.join(extra)}")
    argv = [prog.exe, *(shlex.split(prog.args, posix=False) if prog.args else []), *extra]
    env = None
    if v is not None:
        venv = {k: val for k, val in (v.launch_env(p, prog) or {}).items() if k not in os.environ}
        if venv: env = venv; r.log("launch environment: " + ", ".join(f"{k}={val}" for k, val in venv.items()))
    proc = p.spawn(argv, cwd=str(cwd) if cwd and cwd.is_dir() else None, env=env)
    if v is not None and type(v).watch_program is not vendors.Vendor.watch_program:
        # this vendor observes the live program (Vendor.watch_program). Daemon
        # thread so the GUI can quit mid-run; proc.vstenv_watch lets the CLI
        # join it instead of exiting right after the spawn and killing it.
        def _watch(v=v):
            try: v.watch_program(p, prog, proc)
            except Exception: pass
        t = threading.Thread(target=_watch, daemon=True, name=f"watch:{prog.name}")
        t.start(); proc.vstenv_watch = t
    r.ok(prog.exe); return proc

def uninstall_argv(uninstall: str) -> list[str]:
    """UninstallString -> argv for the prefix. Quoted exe + options, msiexec
    lines, or anything else through cmd /c (installers write shell-ish strings)."""
    s = uninstall.strip()
    if not s: return []
    if s.startswith('"'):
        end = s.find('"', 1)
        if end > 0: return [s[1:end], *shlex.split(s[end + 1:], posix=False)]
    if s.lower().startswith("msiexec"): return shlex.split(s, posix=False)
    if s.lower().startswith("cmd") or "&&" in s or ">" in s: return ["cmd", "/c", s]
    parts = shlex.split(s, posix=False)
    return parts if parts and parts[0].lower().endswith(".exe") else ["cmd", "/c", s]

def uninstall(p: Prefix, prog: Program, reporter=None) -> int:
    """Run the program's own uninstaller interactively and wait."""
    r = null_reporter(reporter)
    argv = uninstall_argv(prog.uninstall)
    if not argv: raise RuntimeError(f"{prog.name} did not register an uninstaller; delete its folder from the prefix by hand")
    r.step(f"Uninstalling {prog.name}")
    cp = p.run(argv, timeout=7200, capture=False)
    # NSIS (and InnoSetup) uninstallers copy themselves to a temp folder, start that
    # copy and return at once; the real work happens after our call comes back.
    wait_for_uninstaller(p, prog)
    (r.ok if cp.returncode == 0 else r.fail)(f"exit {cp.returncode}")
    return cp.returncode

UNINSTALLER_PROC = re.compile(r"(Au_\.exe|_iu[0-9a-z]*\.tmp|unins[0-9]*\.exe|uninstall)", re.I)

def wait_for_uninstaller(p: Prefix, prog: Program, timeout=1800):
    """Block while an uninstaller (or its temp copy) still runs in the prefix."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        procs = [c for _, c in p.processes() if UNINSTALLER_PROC.search(c)]
        if not procs: return
        time.sleep(2)

# An installer's helper: something it started that is still installing after the
# process we ran returned. Wrappers unpack into Temp and run the real setup from
# there (iZotope, InnoSetup's is-*.tmp, NSIS), or hand off to msiexec. A product
# the installer offered to "run now" lives under Program Files and matches none
# of this, so it is not waited for.
INSTALLER_PROC = re.compile(r"(\\temp\\|\\tmp\\|msiexec|\bis-[0-9a-z]+\.tmp|_MEI\d+|setup|instal)", re.I)

# Crash-looping vendor manager/installer chains left behind after an install:
# `start.exe /exec` (Wine's launcher for a program the installer started), a
# still-running installer exe under Downloads, and the Electron helpers it keeps
# respawning (renderer/gpu/utility children with no main). See kill_respawning.
RESPAWNING_MANAGER = re.compile(
    r"(start\.exe /exec|\\Downloads\\.*\.exe|--type=(renderer|gpu-process|utility)|Product Portal|Product Manager)", re.I)

def wait_for_installer(p: Prefix, before: set[int], reporter=None, timeout=7200):
    """Block while a helper the installer started (a pid that was not there before
    it ran) is still installing. Returns the helpers seen."""
    r = null_reporter(reporter); seen = []; t0 = time.time()
    while time.time() - t0 < timeout:
        procs = [c for pid, c in p.processes() if pid not in before and INSTALLER_PROC.search(c)]
        if not procs:
            if seen: r.ok(", ".join(seen))
            return seen
        if not seen: r.step("Waiting for the setup the installer started")
        seen = list(dict.fromkeys(seen + [_exe_name(c) for c in procs]))
        time.sleep(2)
    r.fail(f"{', '.join(seen)} still running after {timeout // 60} min; not waiting any longer")
    return seen

def _exe_name(cmdline: str) -> str:
    """The program in a Windows command line: its first .exe, else its last .tmp/.msi."""
    exe = re.search(r"[^\\/]+?\.exe", cmdline, re.I)
    if exe: return exe.group(0)
    other = re.findall(r"[^\\/ ]+\.(?:tmp|msi)", cmdline, re.I)
    return other[-1] if other else cmdline[:60]

class InstallFailed(RuntimeError):
    """An installer exited non-zero: the environment is left alone."""

class InstallCancelled(InstallFailed):
    """The person cancelled the installer; not an error worth a red failure."""

# Windows installer exit codes that are not failures of ours.
_CANCELLED = {1602}        # ERROR_INSTALL_USEREXIT
_SUCCESS_REBOOT = {3010}   # ERROR_SUCCESS_REBOOT_REQUIRED -- done, as far as Wine cares

def install(p: Prefix, installer: Path, reporter=None) -> int:
    """Run any Windows installer interactively: .msi through msiexec, anything else
    as a program. Plugins it drops are bridged by the caller (yabridge.sync)."""
    r = null_reporter(reporter)
    installer = Path(installer)
    if not installer.is_file(): raise FileNotFoundError(f"installer not found: {installer}")
    r.step(f"Running installer: {installer.name}")
    before = {(x.name, x.install_dir) for x in installed(p)}
    pids_before = {pid for pid, _ in p.processes()}
    if installer.suffix.lower() == ".msi": argv = ["msiexec", "/i", str(installer)]
    else: argv = [str(installer)]
    cp = p.run(argv, timeout=7200, capture=False)
    if cp.returncode in _SUCCESS_REBOOT:
        # The installer finished and asked for a reboot, which a Wine prefix
        # does not need; treat it as the success it is.
        r.ok(f"exit {cp.returncode} (installed; the reboot it asks for is not needed here)")
    elif cp.returncode != 0:
        # An installer that failed wrote nothing worth reacting to, and the
        # steps below -- quirks, bridging, menu entries -- would spend a minute
        # adjusting the environment for an install that did not happen, which
        # reads as if it had. Stop here and say so. Codes vendors use for an
        # ordinary cancel are not failures: 1602 is "user cancelled" and 3010
        # is "needs a reboot", which under Wine means it finished.
        r.fail(f"exit {cp.returncode}")
        if cp.returncode in _CANCELLED:
            raise InstallCancelled(f"{installer.name} was cancelled (exit {cp.returncode})")
        raise InstallFailed(f"{installer.name} failed (exit {cp.returncode}); nothing was changed")
    else:
        r.ok(f"exit {cp.returncode}")
    # The process we ran may be only a wrapper: the real setup it unpacked and
    # started keeps installing after it returns (iZotope's does), and bridging
    # before that finishes misses every plugin it is about to drop.
    wait_for_installer(p, pids_before, r)
    # Some vendor installers leave their Electron manager running (iZotope's
    # Product Portal, launched via `start.exe /exec`), and it crash-loops under
    # Wine: a supervisor relaunches it every few seconds, thrashing the shared
    # wineserver until unrelated plugins deadlock. Stop anything the installer
    # started that is still respawning, but never a program that was already
    # running before this install (a plugin host in a DAW).
    orphans = [c for pid, c in p.processes() if pid not in pids_before and RESPAWNING_MANAGER.search(c)]
    if orphans:
        r.step("Stopping the installer's crash-looping manager")
        n = p.kill_respawning(lambda c: RESPAWNING_MANAGER.search(c) is not None, r)
        r.ok(f"{n} process(es): {', '.join(sorted({_exe_name(c) for c in orphans}))}")
    for prog in installed(p):                      # quirks for what this installer added or replaced, not for
        if not prog.install_dir: continue          # every program already in the prefix (a FabFilter install
        if (prog.name, prog.install_dir) in before: continue   # is not the moment to talk about IK's bundle)
        done = quirks.apply(p, prog.name, prog.install_dir, r)
        # Installers offer "run it now" and start the program before this point:
        # that instance has neither the bundle edits nor the launch arguments
        # (IK Product Manager's first run then fails every request). Replace it.
        if prog.exe and (done or quirks.launch_args(p, prog.name, prog.install_dir)):
            exe = prog.exe.rsplit("\\", 1)[-1]
            if p.is_running(exe):
                r.log(f"{prog.name}: the installer started it without its fixes; restarting it with them")
                p.kill_exe(exe); run(p, prog, r)
    return cp.returncode
