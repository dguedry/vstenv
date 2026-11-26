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
"""
import functools, shutil
from . import winefixes, host
from .progress import null_reporter
from .wine import Prefix

DLL = "wineasio.dll"
KEY = r"HKLM\Software\ASIO\WineASIO"
SYSTEM32_NAME = "wineasio64.dll"

# PipeWire's libjack.so.0: Debian/Ubuntu/Mint, Fedora, Arch. Printed with the host's own
# LD_LIBRARY_PATH (the sandbox's is not the host's). Nothing when PipeWire is not the
# session's sound server or a real JACK server runs: then the system's libjack is right.
_JACK_DIR_SCRIPT = r'''
[ -S "${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/pipewire-0" ] || exit 0
pgrep -x jackd >/dev/null 2>&1 && exit 0
for d in /usr/lib/x86_64-linux-gnu/pipewire-0.3/jack /usr/lib64/pipewire-0.3/jack /usr/lib/pipewire-0.3/jack; do
    [ -e "$d/libjack.so.0" ] && { printf '%s\n%s\n' "$d" "$LD_LIBRARY_PATH"; exit 0; }
done
'''

@functools.lru_cache(maxsize=1)
def pipewire_jack_dir() -> tuple[str, str]:
    """(PipeWire's JACK library directory on the host, the host's LD_LIBRARY_PATH), or ("", "")."""
    lines = host.sh(_JACK_DIR_SCRIPT, timeout=10).splitlines()
    return (lines[0].strip(), lines[1].strip() if len(lines) > 1 else "") if lines and lines[0].strip() else ("", "")

def enabled(p) -> bool:
    """Cheap (no registry read, Prefix.wine_env asks at every launch): the system32 copy register() makes."""
    return (p.drive_c / "windows/system32" / SYSTEM32_NAME).exists()

def jack_env() -> dict:
    """LD_LIBRARY_PATH with PipeWire's JACK library first, for Wine; {} when it is not needed or not there."""
    d, rest = pipewire_jack_dir()
    if not d or d in rest.split(":"): return {}
    return {"LD_LIBRARY_PATH": f"{d}:{rest}" if rest else d}

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
    if registered(p): p.run([p.REG64, "delete", KEY, "/f"], timeout=60)
    r.ok("unregistered"); return True
