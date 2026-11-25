"""WineASIO: an ASIO driver over JACK (PipeWire's libjack) for low-latency audio.

The wine-fixes tarball carries it (winefixes.py): the unix half as
lib/wine/x86_64-unix/wineasio.dll.so and its PE stub as lib/wine/x86_64-windows/
wineasio.dll. What is left for the prefix is the registration, which ASIO hosts
(Cubase: Studio > Studio Setup > Audio System > ASIO Driver = WineASIO) find it by:
HKLM\\Software\\ASIO\\WineASIO, made by regsvr32 from a copy of the stub in system32
named wineasio64.dll. It runs through the prefix's own Wine runner and environment:
Wine commands from a foreign environment start the prefix's services there and break
Steinberg's licence. regsvr32 gets /s, or its success MessageBox blocks.

Wine runs on the host (host.py), so the driver dlopens the host's libjack.so.0 (pipewire-jack or JACK).
"""
import shutil
from . import winefixes
from .progress import null_reporter
from .wine import Prefix

DLL = "wineasio.dll"
KEY = r"HKLM\Software\ASIO\WineASIO"
SYSTEM32_NAME = "wineasio64.dll"

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

def unregister(p: Prefix, reporter=None) -> bool:
    """Undo register(): the system32 copy and the registry key. Nothing happens without a
    prefix or while its wineserver runs (a running Cubase would keep the driver loaded)."""
    r = null_reporter(reporter); r.step("WineASIO registration")
    if not p.exists or p.wineserver_running(): r.skip("no prefix, or Wine is running"); return False
    (p.drive_c / "windows/system32" / SYSTEM32_NAME).unlink(missing_ok=True)
    if registered(p): p.run([p.REG64, "delete", KEY, "/f"], timeout=60)
    r.ok("unregistered"); return True
