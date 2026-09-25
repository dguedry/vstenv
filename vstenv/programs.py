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
merged by name. This prefix is tuned for audio software (Windows 10, real C
runtime, no Wine Mono or Gecko, no 3D layer): programs needing .NET, an embedded
browser or Direct3D will not run, and that is said in the GUI rather than fixed
here, because each of those layers would put the vendors' software at risk.
Vendor modules contribute extra program sources (Vendor.programs).
"""
import re, shlex, struct, time
from dataclasses import dataclass, field
from pathlib import Path
from .progress import null_reporter
from .wine import Prefix
from . import quirks

UNINSTALL_KEYS = (r"Software\Microsoft\Windows\CurrentVersion\Uninstall",
                  r"Software\Wow6432Node\Microsoft\Windows\CurrentVersion\Uninstall")
SKIP_NAMES = re.compile(r"^(Wine (Mono|Gecko)|Microsoft Visual C\+\+|Windows .*Runtime)", re.I)
NOT_A_LAUNCHER = re.compile(r"(unins|uninstall|setup|update|crash|helper)", re.I)
LIMITS = ("Programs that need .NET, an embedded browser (Gecko) or Direct3D will not run here: "
          "this prefix is tuned for audio software and adding those layers would put the vendors' software at risk.")

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

def shortcut_programs(p: Prefix) -> list[Program]:
    roots = [p.drive_c / "ProgramData/Microsoft/Windows/Start Menu/Programs",
             p.user_dir / "AppData/Roaming/Microsoft/Windows/Start Menu/Programs",
             p.drive_c / "users/Public/Desktop", p.user_dir / "Desktop"]
    out, seen = [], set()
    for root in roots:
        if not root.is_dir(): continue
        for lnk in sorted(root.rglob("*.lnk")):
            try: info = parse_lnk(lnk.read_bytes())
            except OSError: continue
            target = info["target"]
            if not target.lower().endswith(".exe"): continue
            low = target.lower()
            if re.search(r"(unins|uninstall|setup|update)", Path(target).name.lower()) or low in seen: continue
            seen.add(low)
            out.append(Program(name=lnk.stem, exe=target, args=info["args"], workdir=info["workdir"] or str(Path(target).parent).replace("/", "\\"),
                               install_dir=str(Path(target).parent).replace("/", "\\"), sources=["shortcut"]))
    return out

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

def installed(p: Prefix) -> list[Program]:
    """Programs in the prefix, merged from the registry and Start Menu shortcuts."""
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
    for l in links:
        match = None
        for r in by_name.values():
            if _norm(l.name) == _norm(r.name) or _bare(l.name) == _bare(r.name) \
           or (r.install_dir and l.exe.lower().startswith(r.install_dir.lower().rstrip("\\") + "\\")):
                match = r; break
        if match:
            match.exe, match.args, match.workdir = l.exe, l.args, l.workdir
            if "shortcut" not in match.sources: match.sources.append("shortcut")
        else: by_name[_norm(l.name)] = l
    for prog in by_name.values():
        if not prog.exe: prog.exe = find_exe(p, prog.install_dir)
        if prog.exe and not prog.workdir: prog.workdir = str(Path(prog.exe).parent).replace("/", "\\")
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
    if not prog.exe: raise RuntimeError(f"{prog.name} has no known launcher (only an uninstaller)")
    if prog.install_dir: quirks.apply(p, prog.name, prog.install_dir, r)
    r.step(f"Starting {prog.name}")
    cwd = p.to_host(prog.workdir) if prog.workdir else None
    extra = quirks.launch_args(p, prog.name, prog.install_dir) if prog.install_dir else []
    if extra: r.log(f"launch arguments: {' '.join(extra)}")
    argv = [prog.exe, *(shlex.split(prog.args, posix=False) if prog.args else []), *extra]
    proc = p.spawn(argv, cwd=str(cwd) if cwd and cwd.is_dir() else None)
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

def install(p: Prefix, installer: Path, reporter=None) -> int:
    """Run any Windows installer interactively: .msi through msiexec, anything else
    as a program. Plugins it drops are bridged by the caller (yabridge.sync)."""
    r = null_reporter(reporter)
    installer = Path(installer)
    if not installer.is_file(): raise FileNotFoundError(f"installer not found: {installer}")
    r.step(f"Running installer: {installer.name}")
    if installer.suffix.lower() == ".msi": argv = ["msiexec", "/i", str(installer)]
    else: argv = [str(installer)]
    cp = p.run(argv, timeout=7200, capture=False)
    (r.ok if cp.returncode == 0 else r.fail)(f"exit {cp.returncode}")
    for prog in installed(p):                      # quirks for whatever just appeared
        if prog.install_dir: quirks.apply(p, prog.name, prog.install_dir, r)
    return cp.returncode
