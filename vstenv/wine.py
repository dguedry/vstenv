"""Portable wine provisioning and Wine prefix management.

One wine build (pinned, downloaded with hash check) is used for everything:
the prefix, Native Access, NI apps, and yabridge. That removes the
"prefix updated by a newer Wine" trap that exists when the prefix's wine and the
host wine drift apart.
"""
import os, re, shutil, subprocess, tarfile, time
from pathlib import Path
from . import paths, host
from .progress import null_reporter

# Pinned build. Kron4ek's builds are portable (built against old glibc) and
# the wow64 flavour needs no 32-bit host libraries. Bump deliberately; the
# NI fixes were verified on wine-staging 9/10/11 so major bumps are low-risk.
WINE_BUILD = {
    "name": "wine-11.17-staging-amd64-wow64",
    "url": "https://github.com/Kron4ek/Wine-Builds/releases/download/11.17/wine-11.17-staging-amd64-wow64.tar.xz",
    "sha256": "278d80f3073f1a81386baafb41b83638d89eedaffb640f31599855145ed4fd7b",
}

class WineBuild:
    def __init__(self, root: Path):
        self.root = Path(root)
    @property
    def wine(self) -> Path:
        for n in ("wine", "wine64"):
            p = self.root / "bin" / n
            if p.exists(): return p
        raise FileNotFoundError(f"no wine binary under {self.root}/bin")
    @property
    def wineserver(self) -> Path: return self.root / "bin" / "wineserver"
    def version(self) -> str:
        try: return subprocess.run([str(self.wine), "--version"], capture_output=True, text=True, timeout=20).stdout.strip()
        except Exception: return "unknown"

RELEASE_REPO = "dguedry/vstenv"     # every release carries the Wine tarball yabridge was built against

def wine_tarball_sources(build=WINE_BUILD) -> list[tuple[str, str]]:
    """Where to get the pinned Wine, in order: the app's own latest release (the
    tested tarball, served with the app), then the upstream build server. The
    sha256 is the same either way, so a swapped file is refused."""
    from .download import text
    import json
    name = build["name"] + ".tar.xz"; out = []
    try:
        rel = json.loads(text(f"https://api.github.com/repos/{RELEASE_REPO}/releases/latest"))
        a = next((a for a in rel.get("assets", []) if a.get("name") == name), None)
        if a: out.append((a["browser_download_url"], f"{RELEASE_REPO} release {rel.get('tag_name', '')}"))
    except Exception: pass
    out.append((build["url"], "upstream build"))
    return out

def provision(reporter=None, build=WINE_BUILD) -> WineBuild:
    """Download+extract the pinned wine build if missing. Returns the build."""
    r = null_reporter(reporter); paths.ensure_dirs()
    dest = paths.WINE_DIR / build["name"]
    if (dest / "bin").exists():
        return WineBuild(dest)
    from .download import fetch
    r.step(f"Downloading wine ({build['name']})")
    tarball = None; errors = []
    for url, label in wine_tarball_sources(build):
        try: tarball = fetch(url, paths.DOWNLOADS / (build["name"] + ".tar.xz"), sha256=build["sha256"], reporter=r, label=f"wine ({label})"); break
        except Exception as e: errors.append(f"{label}: {str(e)[:80]}"); r.log(f"{label}: {str(e)[:100]}")
    if tarball is None: raise RuntimeError("could not download the Wine build: " + "; ".join(errors))
    r.ok(label)
    r.step("Extracting wine")
    tmp = paths.WINE_DIR / (build["name"] + ".tmp")
    shutil.rmtree(tmp, ignore_errors=True); tmp.mkdir(parents=True)
    with tarfile.open(tarball, "r:xz") as t:
        t.extractall(tmp, filter="tar")
    inner = next(p for p in tmp.iterdir() if p.is_dir())
    inner.rename(dest); shutil.rmtree(tmp, ignore_errors=True)
    r.ok(dest.name)
    return WineBuild(dest)

# Wine's unix-side modules link host libraries. The core needs only glibc and X11;
# the rest is optional and a distribution may lack a library version a module
# was built against (winedmo: FFmpeg 4). Which of these matters:
ESSENTIAL_MODULES = ("winex11", "winevulkan", "opengl32", "dwrite", "win32u", "ntdll")
AUDIO_MODULES = ("winepulse", "winealsa")           # one of them must load

def missing_module_libs(build: WineBuild) -> dict[str, list[str]]:
    """{module: [missing host libraries]} for the build's unix modules, resolved on
    the host (where Wine runs). ntdll.so and win32u.so are Wine's own and are
    found by its loader, not by ldd, so they are not counted."""
    d = build.root / "lib/wine/x86_64-unix"
    out = host.sh(f'cd {str(d)!r} && for f in *.so; do echo "== $f"; ldd "$f" 2>/dev/null | grep "not found"; done', timeout=120)
    res: dict[str, list[str]] = {}; cur = None
    for line in out.splitlines():
        if line.startswith("== "): cur = line[3:].removesuffix(".so"); continue
        lib = line.split()[0] if line.split() else ""
        if cur and lib and lib not in ("ntdll.so", "win32u.so"): res.setdefault(cur, []).append(lib)
    return res

def module_libs_check(missing: dict[str, list[str]]) -> tuple[bool, str]:
    """(ok, detail): not ok when an essential module or all audio modules lost a library."""
    essential = [m for m in ESSENTIAL_MODULES if m in missing]
    audio_gone = all(m in missing for m in AUDIO_MODULES)
    if essential or audio_gone:
        bad = essential + ([m for m in AUDIO_MODULES if m in missing] if audio_gone else [])
        return False, "; ".join(f"{m} needs {', '.join(missing[m])}" for m in bad)
    if not missing: return True, "every module's host libraries are present"
    return True, "optional modules unavailable here: " + "; ".join(f"{m} ({', '.join(v)})" for m, v in sorted(missing.items()))

def installed_build() -> WineBuild | None:
    dest = paths.WINE_DIR / WINE_BUILD["name"]
    return WineBuild(dest) if (dest / "bin").exists() else None


class Prefix:
    """A Wine prefix driven by a specific WineBuild.

    All calls set WINEFSYNC=1 and disable winemenubuilder (no .desktop spam).
    Registry writes go through the explicit 64-bit reg.exe so they land in the
    64-bit HKLM view that NI's 64-bit apps read.
    """
    REG64 = r"C:\windows\system32\reg.exe"

    def __init__(self, path: Path, build: WineBuild):
        self.path = Path(path); self.build = build
    @property
    def drive_c(self) -> Path: return self.path / "drive_c"
    @property
    def exists(self) -> bool: return (self.drive_c / "windows").exists()

    # One line, the wine this prefix was built with. yabridge's host launcher reads
    # it (see yabridge.py) so a DAW runs this prefix's plugins with this wine.
    WINELOADER_FILE = "wineloader"
    @property
    def wineloader_file(self) -> Path: return self.path / self.WINELOADER_FILE

    def declare_wine(self, reporter=None) -> bool:
        """Record this prefix's wine in <prefix>/wineloader. Idempotent; True if written."""
        r = null_reporter(reporter)
        r.step("Recording the prefix's wine for plugin hosts")
        want = f"{self.build.wine}\n"
        try: cur = self.wineloader_file.read_text(errors="replace")
        except OSError: cur = None
        if cur == want: r.skip("recorded"); return False
        self.wineloader_file.write_text(want); r.ok(str(self.build.wine)); return True

    def dll_overrides(self, mono: bool | None = None) -> str:
        """No .desktop spam, no Gecko (nothing the supported vendors ship needs IE).
        mscoree stays disabled, which also keeps wineboot from asking about Wine
        Mono, until Mono is installed in the prefix (mono.py): Steinberg's
        installer tooling is .NET and runs under it."""
        if mono is None: mono = (self.drive_c / "windows/mono").is_dir()
        return "winemenubuilder.exe=d;mshtml=d" + ("" if mono else ";mscoree=d")

    def wine_env(self, extra: dict | None = None, debug="-all") -> dict:
        """Only the variables Wine needs: these are added to the *host* session
        environment when the sandbox runs Wine through flatpak-spawn (see host.py)."""
        e = {
            "WINEPREFIX": str(self.path), "WINEFSYNC": "1", "WINEDEBUG": debug,
            "WINEDLLOVERRIDES": self.dll_overrides(),
            "WINEARCH": "win64",
        }
        if extra: e.update(extra)
        return e

    def env(self, extra: dict | None = None, debug="-all") -> dict:
        """Full environment for tools that run *in the sandbox* and shell out to
        `wine` themselves (yabridgectl): ours first on PATH."""
        e = dict(os.environ); e.update(self.wine_env(extra, debug))
        e["PATH"] = f"{self.build.root / 'bin'}:{e.get('PATH', '')}"
        return e

    def run(self, args: list[str], *, timeout=600, capture=True, env: dict | None = None,
            debug="-all", cwd=None, stderr_to=None) -> subprocess.CompletedProcess:
        """Run a Windows program in the prefix and wait. Wine runs on the host
        (one pid namespace with DAW plugins), see host.py."""
        kw = dict(timeout=timeout)
        if stderr_to is not None:
            with open(stderr_to, "wb") as f:
                return host.run([str(self.build.wine), *args], env=self.wine_env(env, debug), cwd=cwd,
                                stdout=subprocess.DEVNULL, stderr=f, **kw)
        return host.run([str(self.build.wine), *args], env=self.wine_env(env, debug), cwd=cwd,
                        capture_output=capture, text=capture, **kw)

    def spawn(self, args: list[str], *, env: dict | None = None, log: Path | None = None, cwd=None) -> subprocess.Popen:
        """Start a Windows program detached (GUI apps). The returned process ends
        when the program does (flatpak-spawn waits for its host command)."""
        out = open(log, "ab") if log else subprocess.DEVNULL
        return host.popen([str(self.build.wine), *args], env=self.wine_env(env), cwd=cwd, stdout=out, stderr=out,
                          stdin=subprocess.DEVNULL, start_new_session=True)

    # -- lifecycle ---------------------------------------------------------------
    def create(self, reporter=None, windows_version="win10"):
        r = null_reporter(reporter)
        r.step("Creating Wine prefix")
        if self.exists: r.skip("exists"); return
        self.path.mkdir(parents=True, exist_ok=True)
        self.run(["wineboot", "-u"], timeout=900)
        self.run(["winecfg", "/v", windows_version], timeout=120)
        self.wait_idle(); r.ok(str(self.path))

    def wait_idle(self, timeout=120):
        """Wait for wineserver to exit so registry hives are flushed."""
        try: host.run([str(self.build.wineserver), "-w"], env=self.wine_env(), timeout=timeout)
        except subprocess.TimeoutExpired: pass

    def kill(self):
        host.run([str(self.build.wineserver), "-k"], env=self.wine_env(), timeout=60)

    # /proc is scanned on the host: the sandbox's /proc shows only the sandbox,
    # and every Wine process of ours now lives on the host.
    PROC_SCAN = r"""for p in /proc/[0-9]*; do
  tr '\0' '\n' < "$p/environ" 2>/dev/null | grep -qxF -- "WINEPREFIX=$VSTENV_PREFIX" || continue
  printf '%s\t' "${p#/proc/}"; tr '\0' ' ' < "$p/cmdline" 2>/dev/null; echo
done"""

    def processes(self, exe_name: str | None = None) -> list[tuple[int, str]]:
        """(pid, cmdline) of processes running with *this* prefix (WINEPREFIX in
        their environment), host-wide."""
        out = []
        for line in host.sh(self.PROC_SCAN, env={"VSTENV_PREFIX": str(self.path)}).splitlines():
            pid, _, cmd = line.partition("\t")
            if not pid.isdigit(): continue
            cmd = cmd.strip()
            if exe_name is None or exe_name.lower() in cmd.lower(): out.append((int(pid), cmd))
        return out

    def is_running(self, exe_name: str) -> bool:
        return any(not c.startswith(("python", "/usr/bin/python", "bash", "/bin/bash", "sh", "/bin/sh"))
                   for _, c in self.processes(exe_name))

    def wineserver_dir(self) -> Path:
        """wineserver's per-prefix dir: $XDG_RUNTIME_DIR/wine/server-<dev>-<inode>
        (modern Wine) or /tmp/.wine-<uid>/server-<dev>-<inode> (fallback)."""
        st = self.path.stat(); name = f"server-{st.st_dev:x}-{st.st_ino:x}"
        run = os.environ.get("XDG_RUNTIME_DIR")
        cands = ([Path(run) / "wine" / name] if run else []) + [Path(f"/tmp/.wine-{os.getuid()}") / name]
        for c in cands:
            if (c / "lock").exists(): return c
        return cands[0]

    def wineserver_running(self) -> bool:
        """True if a wineserver for *this* prefix is up. wineserver holds an fcntl
        write lock on <dir>/lock for its lifetime; visible even from another
        Flatpak instance (processes are namespaced, /tmp is shared)."""
        lock = self.wineserver_dir() / "lock"
        if not lock.exists(): return False
        import fcntl
        try:
            with open(lock, "r+b") as f:
                try:
                    fcntl.lockf(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    fcntl.lockf(f, fcntl.LOCK_UN); return False   # nobody held it
                except OSError: return True
        except OSError: return False

    def lock_holders(self) -> list[int] | None:
        """Pids holding this prefix's wineserver lock, read from the host's
        /proc/locks (the kernel names the holder of every fcntl lock, whatever
        its environment looks like). [] when nobody holds it or the pid is in a
        namespace the host cannot see (shown as 0); None when /proc/locks could
        not be read, so the caller can tell 'no holder' from 'no answer'."""
        try: st = (self.wineserver_dir() / "lock").stat()
        except OSError: return []
        key = f"{os.major(st.st_dev):02x}:{os.minor(st.st_dev):02x}:{st.st_ino}"
        out = host.sh("cat /proc/locks && echo __locks_read__")
        if "__locks_read__" not in out: return None
        pids = []
        for line in out.splitlines():
            f = line.split()          # "1: POSIX ADVISORY WRITE <pid> <maj>:<min>:<ino> <start> <end>"; waiters start with "->"
            if len(f) >= 6 and f[0].endswith(":") and f[5] == key and f[4].isdigit() and int(f[4]) > 0: pids.append(int(f[4]))
        return pids

    def wineserver_scope(self) -> str:
        """Where this prefix's wineserver lives relative to us:
        'none'    - no wineserver is up for this prefix
        'ours'    - up, and its process is visible from the host (same pid
                    namespace as the Wine we run: we can use it)
        'foreign' - up (holds the lock) but its holder is in a pid namespace the
                    host cannot see (a sandboxed DAW with its own Wine?).
                    wineserver addresses its clients by pid (tgkill, ptrace,
                    process_vm_readv), so a client from another namespace gets no
                    APCs or suspends: Electron's renderer dies at once and the
                    server can spin forever. Our own Wine runs on the host for
                    exactly this reason (host.py).
        'unknown' - up, but neither /proc/locks nor the process scan could be
                    read just now; callers should not treat this as foreign.
        The lock holder from /proc/locks is the primary evidence; the WINEPREFIX
        scan of /proc is the fallback, since a holder started with an unusual
        environment would otherwise look foreign."""
        if not self.wineserver_running(): return "none"
        holders = self.lock_holders()
        if holders: return "ours"
        if any("wineserver" in cmd for _, cmd in self.processes()): return "ours"
        return "unknown" if holders is None else "foreign"

    def kill_exe(self, exe_name: str, wait=3.0):
        pids = [str(pid) for pid, _ in self.processes(exe_name)]
        if pids: host.run(["kill", "-TERM", *pids], capture_output=True, timeout=20)
        time.sleep(wait)

    def kill_pids(self, pids, sig="TERM", wait=2.0):
        pids = [str(x) for x in pids]
        if pids: host.run(["kill", f"-{sig}", *pids], capture_output=True, timeout=20)
        if wait: time.sleep(wait)

    def kill_respawning(self, match, reporter=None, rounds=4) -> int:
        """Stop a set of prefix processes that a supervisor keeps relaunching.

        A crashing Electron app (a vendor installer's manager: iZotope's Product
        Portal, IK's Product Manager) is restarted every few seconds by the
        process that spawned it, so killing the children alone loops forever and
        the churn starves the shared wineserver -- enough to deadlock unrelated
        plugins (an Ozone preset window froze this way, 2026-09-29). Kill parents
        before children each round (so nothing is left to respawn them), TERM then
        KILL, and confirm the set stays empty. `match` is a predicate on the
        command line. Returns how many distinct pids were killed."""
        from .progress import null_reporter
        r = null_reporter(reporter); killed = set()
        def victims():
            # pid -> (ppid, cmd) for prefix processes whose cmdline matches, plus
            # the non-system parents that launch them (start.exe /exec, the installer).
            hits = [(pid, cmd) for pid, cmd in self.processes() if match(cmd)]
            return hits
        for i in range(rounds):
            hits = victims()
            if not hits:
                if killed: r.log(f"stopped {len(killed)} respawning process(es)")
                return len(killed)
            pids = [pid for pid, _ in hits]
            killed.update(pids)
            self.kill_pids(pids, "TERM" if i < rounds - 1 else "KILL", wait=2.0 + i)
        left = victims()
        if left: r.log(f"warning: {len(left)} process(es) still present after {rounds} rounds")
        return len(killed)

    # -- integrity -------------------------------------------------------------------
    # Builtin DLLs whose prefix copies must come from *this* wine. Another Wine
    # (a DAW's yabridge using the host wine) touching the prefix runs its own
    # prefix update and replaces them; mixed DLLs break 32-bit programs among
    # other things (NI installers fail at once with "Cannot create ..." dialogs).
    PROBE_DLLS = (("syswow64", "i386-windows", "comctl32.dll"), ("system32", "x86_64-windows", "comctl32.dll"),
                  ("syswow64", "i386-windows", "user32.dll"), ("system32", "x86_64-windows", "ntdll.dll"))

    def foreign_dlls(self) -> list[str]:
        """Prefix builtin DLLs that differ in size from this wine build's copies."""
        bad = []
        for sysdir, pedir, dll in self.PROBE_DLLS:
            ours = self.build.root / "lib/wine" / pedir / dll; mine = self.drive_c / "windows" / sysdir / dll
            if ours.exists() and mine.exists() and ours.stat().st_size != mine.stat().st_size: bad.append(f"{sysdir}/{dll}")
        return bad

    def refresh_builtins(self, reporter=None) -> bool:
        """Re-run this wine's prefix update so builtin DLLs match it again. Native
        DLLs installed by the app (ucrtbase, VC runtime) are not fake DLLs and are
        left alone by wineboot. Returns True if a refresh was needed."""
        r = null_reporter(reporter)
        r.step("Prefix files match this wine")
        bad = self.foreign_dlls()
        if not bad: r.skip(); return False
        (self.path / ".update-timestamp").unlink(missing_ok=True)
        self.run(["wineboot", "-u"], timeout=900)
        still = self.foreign_dlls()
        (r.fail if still else r.ok)(f"refreshed ({', '.join(bad)} were from another Wine)" if not still else f"still foreign: {still}")
        return True

    # -- paths ---------------------------------------------------------------------
    @property
    def user_dir(self) -> Path:
        """drive_c/users/<name> (Wine uses the unix login name)."""
        users = self.drive_c / "users"
        for cand in (os.environ.get("USER", ""), "steamuser"):
            if cand and (users / cand).exists(): return users / cand
        for p in users.iterdir():
            if p.is_dir() and p.name != "Public": return p
        return users / (os.environ.get("USER") or "user")
    @property
    def public_docs(self) -> Path: return self.drive_c / "users" / "Public" / "Documents"
    def to_host(self, winpath: str) -> Path:
        """Windows path -> host path. C: is drive_c; other letters go through
        dosdevices (Z: is / by default), so Z:\\home\\me maps to /home/me."""
        p = winpath.replace("\\", "/")
        m = re.match(r"^([A-Za-z]):/?(.*)$", p)
        if not m: return self.drive_c / p.lstrip("/")
        drive, rest = m.group(1).lower(), m.group(2)
        root = self.drive_c if drive == "c" else self.path / "dosdevices" / f"{drive}:"
        return root / rest
    def to_win(self, host: Path) -> str:
        rel = Path(host).resolve().relative_to(self.drive_c.resolve())
        return "C:\\" + str(rel).replace("/", "\\")

    # -- registry -------------------------------------------------------------------
    def reg_add(self, key: str, name: str, value: str, kind="REG_SZ"):
        cp = self.run([self.REG64, "add", key, "/v", name, "/t", kind, "/d", str(value), "/f"], timeout=60)
        if cp.returncode != 0: raise RuntimeError(f"reg add failed: {key}\\{name}: {cp.stderr.strip()}")

    def reg_set_default(self, key: str, value: str, kind="REG_SZ"):
        """Set a key's (Default) value. reg add /ve, not /v (which needs a name)."""
        cp = self.run([self.REG64, "add", key, "/ve", "/t", kind, "/d", str(value), "/f"], timeout=60)
        if cp.returncode != 0: raise RuntimeError(f"reg set default failed: {key}: {cp.stderr.strip()}")
    def reg_query(self, key: str) -> dict[str, str]:
        cp = self.run([self.REG64, "query", key], timeout=60)
        vals = {}
        for line in cp.stdout.splitlines():
            m = re.match(r"\s+(\S.*?)\s{2,}(REG_\w+)\s{2,}(.*)$", line)
            if m: vals[m.group(1)] = m.group(3).strip()
        return vals
    def reg_import(self, reg_text: str, name="vstenv-import.reg"):
        f = self.drive_c / name
        f.write_text(reg_text, encoding="utf-8")
        cp = self.run(["regedit", f"C:\\{name}"], timeout=120)
        return cp.returncode

    def reg_import_values(self, values: dict[str, dict[str, tuple[str, str]]], name="vstenv-values.reg") -> int:
        """Write many values in one regedit call. values: {'HKLM\\Software\\X': {'Name': ('REG_SZ'|'REG_DWORD'|'REG_EXPAND_SZ', data)}}"""
        def esc(v): return v.replace("\\", "\\\\").replace('"', '\\"')
        lines = ["Windows Registry Editor Version 5.00", ""]
        for key, vals in values.items():
            root, _, rest = key.partition("\\")
            root = {"HKLM": "HKEY_LOCAL_MACHINE", "HKCU": "HKEY_CURRENT_USER"}.get(root, root)
            lines.append(f"[{root}\\{rest}]")
            for n, (kind, data) in vals.items():
                if kind == "REG_DWORD": lines.append(f'"{esc(n)}"=dword:{int(data):08x}')
                elif kind == "REG_EXPAND_SZ":
                    hexs = ",".join(f"{b:02x}" for b in (str(data) + "\0").encode("utf-16-le")); lines.append(f'"{esc(n)}"=hex(2):{hexs}')
                else: lines.append(f'"{esc(n)}"="{esc(str(data))}"')
            lines.append("")
        return self.reg_import("\r\n".join(lines), name)

    # -- services --------------------------------------------------------------------
    def sc(self, *args) -> str:
        return self.run(["sc", *args], timeout=60).stdout
