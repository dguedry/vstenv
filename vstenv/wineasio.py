"""WineASIO: an ASIO driver over JACK (PipeWire's libjack) for low-latency audio.

The wine-fixes tarball carries it (winefixes.py): the unix half as
lib/wine/x86_64-unix/wineasio.dll.so and its PE stub as lib/wine/x86_64-windows/
wineasio.dll. What is left for the prefix is the registration, which ASIO hosts
(Cubase: Studio > Studio Setup > Audio System > ASIO Driver = WineASIO) find it by:
HKLM\\Software\\ASIO\\WineASIO, made by regsvr32 from a copy of the stub in system32
named wineasio64.dll. It runs through the prefix's own Wine runner and environment:
Wine commands from a foreign environment start the prefix's services there and break
Steinberg's licence. regsvr32 gets /s, or its success MessageBox blocks.

Opt-in (`vstenv wineasio enable`): once registered, every ASIO-capable program in
the prefix lists it, and one that picks it has no sound where JACK is not reachable.
Setup and `wine-fixes install` only keep an existing registration's copy current.

Wine runs on the host (host.py), so the driver dlopens the host's libjack.so.0. On
Debian, Ubuntu and Mint that name is JACK2's library even when PipeWire is the sound
server: PipeWire's replacement sits in a private directory that only `pw-jack` adds,
and WineASIO fails with "Unable to open a JACK client". jack_env() gives Wine that
directory (Prefix.wine_env adds it while the driver is registered).

Settings. The driver reads six values from HKCU\\Software\\Wine\\WineASIO each time a
program opens it (SETTINGS below). Its Control Panel button runs a host program named
`wineasio-settings`; upstream's is a PyQt tool that edits ~/.wine unless told otherwise.
Ours is a two-line launcher (LAUNCHER, written by register(), on Wine's PATH while the
driver is registered) that opens this app's own settings window for this prefix
(wineasio_gui.py); `vstenv wineasio set` changes the same values from a terminal.
"""
import functools, os, shutil
from . import winefixes, host, paths
from .progress import null_reporter
from .wine import Prefix

DLL = "wineasio.dll"
KEY = r"HKLM\Software\ASIO\WineASIO"
SYSTEM32_NAME = "wineasio64.dll"

# What Wine's environment needs from the host, in one call: PipeWire's libjack.so.0
# directory (Debian/Ubuntu/Mint, Fedora, Arch; empty when PipeWire is not the session's
# sound server or a real JACK server runs: then the system's libjack is right), and the
# host's own LD_LIBRARY_PATH and PATH (the sandbox's are not the host's).
_HOST_SCRIPT = r'''
jack=
if [ -S "${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/pipewire-0" ] && ! pgrep -x jackd >/dev/null 2>&1; then
    for d in /usr/lib/x86_64-linux-gnu/pipewire-0.3/jack /usr/lib64/pipewire-0.3/jack /usr/lib/pipewire-0.3/jack; do
        [ -e "$d/libjack.so.0" ] && { jack=$d; break; }
    done
fi
printf '%s\n%s\n%s\n' "$jack" "$LD_LIBRARY_PATH" "$PATH"
'''

@functools.lru_cache(maxsize=1)
def _host_facts() -> tuple[str, str, str]:
    lines = [l.strip() for l in host.sh(_HOST_SCRIPT, timeout=10).splitlines()] + ["", "", ""]
    return lines[0], lines[1], lines[2]

def pipewire_jack_dir() -> tuple[str, str]:
    """(PipeWire's JACK library directory on the host, the host's LD_LIBRARY_PATH); ("", ...) when not wanted."""
    jack, ld, _ = _host_facts()
    return jack, ld

def enabled(p) -> bool:
    """Cheap (no registry read, Prefix.wine_env asks at every launch): the system32 copy register() makes."""
    return (p.drive_c / "windows/system32" / SYSTEM32_NAME).exists()

def jack_env() -> dict:
    """LD_LIBRARY_PATH with PipeWire's JACK library first, for Wine; {} when it is not needed or not there."""
    d, rest = pipewire_jack_dir()
    if not d or d in rest.split(":"): return {}
    return {"LD_LIBRARY_PATH": f"{d}:{rest}" if rest else d}

# The launcher the driver's Control Panel button runs (execvp("wineasio-settings")), alone
# in a directory of its own so that nothing else on Wine's PATH changes.
BIN = paths.DATA / "wineasio-bin"
LAUNCHER = BIN / "wineasio-settings"

def write_launcher() -> None:
    from . import menu
    BIN.mkdir(parents=True, exist_ok=True)
    text = f"#!/bin/sh\n# Written by vstenv: WineASIO's Control Panel button runs this.\nexec {menu.launcher()} wineasio settings\n"
    if not LAUNCHER.exists() or LAUNCHER.read_text() != text: LAUNCHER.write_text(text)
    LAUNCHER.chmod(0o755)

def launch_env() -> dict:
    """What Wine needs while the driver is registered: PipeWire's JACK library, and the launcher on PATH."""
    e = jack_env()
    _, _, path = _host_facts()
    if LAUNCHER.exists() and path and str(BIN) not in path.split(":"): e["PATH"] = f"{BIN}:{path}"
    return e

# name -> (registry value, default, lowest, highest, what it is). All REG_DWORD; the defaults are the driver's.
KEY_SETTINGS = r"HKCU\Software\Wine\WineASIO"
BUFFER_SIZES = (16, 32, 64, 128, 256, 512, 1024, 2048, 4096, 8192)
SETTINGS = {
    "inputs":           ("Number of inputs", 16, 0, 128, "input channels the driver offers"),
    "outputs":          ("Number of outputs", 16, 0, 128, "output channels the driver offers"),
    "buffer":           ("Preferred buffersize", 1024, 16, 8192, "buffer size in samples offered to the program; only used with fixed-buffer 0"),
    "fixed-buffer":     ("Fixed buffersize", 1, 0, 1, "1: the buffer size is JACK's/PipeWire's and the program cannot change it; 0: the program may set it"),
    "connect-hardware": ("Connect to hardware", 1, 0, 1, "1: connect the channels to the sound card's ports when the driver starts"),
    "autostart-jack":   ("Autostart server", 0, 0, 1, "1: start a JACK server when none runs (not needed with PipeWire)"),
}

def settings(p: Prefix) -> dict[str, int]:
    """Every setting's value; the driver's default where the registry has none."""
    try: have = p.reg_query(KEY_SETTINGS) if p.exists else {}
    except Exception: have = {}
    out = {}
    for name, (value, default, lo, hi, _) in SETTINGS.items():
        try: out[name] = min(max(int(have[value]), lo), hi)
        except (KeyError, ValueError): out[name] = default
    return out

def check(name: str, value) -> int:
    """The value as the driver takes it, or ValueError saying what is allowed."""
    if name not in SETTINGS: raise ValueError(f"no such setting: {name} (one of: {', '.join(SETTINGS)})")
    _, _, lo, hi, _ = SETTINGS[name]
    try: v = int(str(value).strip())
    except ValueError: raise ValueError(f"{name}: a whole number from {lo} to {hi}") from None
    if not lo <= v <= hi: raise ValueError(f"{name}: from {lo} to {hi}")
    if name == "buffer" and v not in BUFFER_SIZES: raise ValueError(f"buffer: one of {', '.join(map(str, BUFFER_SIZES))}")
    return v

def set_setting(p: Prefix, name: str, value) -> int:
    """Write one setting. A program that has the driver open reads it the next time it opens the driver."""
    v = check(name, value)
    p.reg_add(KEY_SETTINGS, SETTINGS[name][0], str(v), kind="REG_DWORD")
    return v

def registered(p: Prefix) -> bool:
    try: return bool(p.reg_query(KEY))
    except Exception: return False

def register(p: Prefix, reporter=None) -> bool:
    """Copy the stub into the prefix's system32 and register it. Idempotent: nothing
    runs when the copy is current and the key exists. False when WineASIO is not in the build."""
    r = null_reporter(reporter); r.step("WineASIO (ASIO driver over JACK/PipeWire)")
    if not p.exists: r.skip("no prefix yet (vstenv setup)"); return False
    if not winefixes.has(p.build, DLL) or not winefixes.has(p.build, "x86_64-unix/wineasio.dll.so"):
        r.skip("not in this Wine build (vstenv wine-fixes install)"); return False
    try: write_launcher()
    except OSError: pass        # the Control Panel button then does nothing; the driver still works
    src, dst = winefixes.dest(p.build, DLL), p.drive_c / "windows/system32" / SYSTEM32_NAME
    copied = not dst.exists() or dst.read_bytes() != src.read_bytes()
    if copied: dst.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(src, dst)
    if not copied and registered(p): r.skip("registered"); return True
    cp = p.run(["regsvr32", "/s", SYSTEM32_NAME], timeout=120)
    if cp.returncode != 0 or not registered(p): r.fail(f"regsvr32 {SYSTEM32_NAME} failed ({cp.returncode})"); return False
    r.ok("registered; pick WineASIO as the ASIO driver"); return True

def refresh(p: Prefix, reporter=None) -> bool:
    """Setup and `wine-fixes install`: keep a registration the user asked for current, never make one."""
    r = null_reporter(reporter)
    if not p.exists or not enabled(p):
        r.step("WineASIO (ASIO driver over JACK/PipeWire)"); r.skip("off (turn it on: vstenv wineasio enable)"); return False
    return register(p, r)

def unregister(p: Prefix, reporter=None) -> bool:
    """Undo register(): the system32 copy and the registry key. Nothing happens without a
    prefix or while its wineserver runs (a running Cubase would keep the driver loaded)."""
    r = null_reporter(reporter); r.step("WineASIO registration")
    if not p.exists or p.wineserver_running(): r.skip("no prefix, or Wine is running"); return False
    (p.drive_c / "windows/system32" / SYSTEM32_NAME).unlink(missing_ok=True)
    LAUNCHER.unlink(missing_ok=True)
    if registered(p): p.run([p.REG64, "delete", KEY, "/f"], timeout=60)
    r.ok("unregistered"); return True
