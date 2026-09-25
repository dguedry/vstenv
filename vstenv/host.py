"""Run Wine on the host from inside the Flatpak sandbox.

wineserver addresses its clients by pid (tgkill, ptrace, process_vm_readv).
Every `flatpak run` is its own pid namespace and the host is another, so a
wineserver in one namespace cannot serve a client in another: Electron's
renderer dies at once, Chromium's process broker fails, the server can spin
forever. DAWs load NI plugins on the host, so the prefix's wineserver has to
live on the host, and therefore so does every Wine process this app starts.

flatpak-spawn --host asks the Flatpak session helper to run a command in the
host session (permission --talk-name=org.freedesktop.Flatpak). The binary is
still the app's own pinned Wine build under the app's data directory, never
the distribution's wine. Outside Flatpak commands run directly.
"""
import os, shutil, subprocess
from pathlib import Path

FLATPAK_INFO = Path("/.flatpak-info")
# Variables that must never reach a Wine program. VS Code exports the first in its
# terminals; an Electron app started with it runs as plain Node and exits at once.
UNSET = ("ELECTRON_RUN_AS_NODE",)

def in_flatpak() -> bool:
    return FLATPAK_INFO.exists()

def wrap(cmd: list[str], env: dict | None = None, cwd: str | os.PathLike | None = None) -> list[str]:
    """The argv that runs `cmd` on the host with `env` added to the host session
    environment (unchanged outside Flatpak; the caller merges env then)."""
    if not in_flatpak(): return list(cmd)
    out = ["flatpak-spawn", "--host", *(f"--unset-env={v}" for v in UNSET)]
    if cwd: out.append(f"--directory={cwd}")
    out += [f"--env={k}={v}" for k, v in (env or {}).items()]
    return out + ["--", *cmd]

def _kw(env: dict | None, cwd, kw: dict) -> dict:
    if in_flatpak():
        kw.pop("cwd", None)             # passed as --directory
    else:
        merged = {k: v for k, v in os.environ.items() if k not in UNSET}
        if env: merged.update(env)
        kw["env"] = merged
        if cwd: kw["cwd"] = cwd
    return kw

def run(cmd: list[str], env: dict | None = None, cwd=None, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(wrap(cmd, env, cwd), **_kw(env, cwd, kw))

def popen(cmd: list[str], env: dict | None = None, cwd=None, **kw) -> subprocess.Popen:
    return subprocess.Popen(wrap(cmd, env, cwd), **_kw(env, cwd, kw))

def sh(script: str, env: dict | None = None, timeout=60) -> str:
    """Run a POSIX sh script on the host (for /proc scans: the sandbox's /proc
    only shows the sandbox). Returns stdout."""
    try:
        return run(["sh", "-c", script], env=env, capture_output=True, text=True, timeout=timeout).stdout
    except (subprocess.TimeoutExpired, OSError):
        return ""

def available() -> tuple[bool, str]:
    """Can we reach the host? Outside Flatpak always; inside, only with the
    org.freedesktop.Flatpak portal permission."""
    if not in_flatpak(): return True, "not sandboxed: Wine runs directly"
    if not shutil.which("flatpak-spawn"): return False, "flatpak-spawn not found in the sandbox"
    try:
        cp = subprocess.run(["flatpak-spawn", "--host", "--", "true"], capture_output=True, text=True, timeout=20)
    except (subprocess.TimeoutExpired, OSError) as e:
        return False, f"flatpak-spawn --host failed: {e}"
    if cp.returncode != 0:
        return False, "the sandbox may not run commands on the host (needs --talk-name=org.freedesktop.Flatpak): " + (cp.stderr.strip().splitlines() or ["?"])[-1]
    return True, "Wine processes run on the host (flatpak-spawn --host), in the same pid namespace as DAW plugins"
