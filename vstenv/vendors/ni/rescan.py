"""NI controllers plugged in after the Wine session started.

NIHardwareService and NIHostIntegrationAgent look for controllers once, when
they start, and learn of later ones only from SERVICE_CONTROL_DEVICEEVENT,
which Wine never sends to a service. Wine also starts them before its USB and
HID devices exist. So a Komplete Kontrol keyboard or a Maschine is not seen
until both services are restarted by hand (the S88 MK1, 2026-10-31).

nirescan.exe (tools/nirescan, from ni-wine) is a service that watches for NI
devices (vendor id 17CC) and restarts both services once they are up, one
restart per burst of arrivals. On a Wine that does send the event it does
nothing. It ships in the wine-fixes package and is copied into the prefix.
"""
from __future__ import annotations

import filecmp, shutil
from pathlib import Path

from ...progress import null_reporter
from ...wine import Prefix
from ... import winefixes

NAME = "nirescan"
EXE = "nirescan.exe"
SERVICES_KEY = r"HKLM\System\CurrentControlSet\Services"
NI_SERVICES = ("NIHardwareService", "NIHostIntegrationAgent")


def source(p: Prefix) -> Path | None:
    """The packaged nirescan.exe in the Wine build, None until wine-fixes has it."""
    if p.build is None or not winefixes.has(p.build, EXE): return None
    f = winefixes.dest(p.build, EXE)
    return f if f.is_file() else None


def target(p: Prefix) -> Path: return p.drive_c / "windows/system32" / EXE


def ni_hardware_services(p: Prefix) -> list[str]:
    """NI's hardware services registered in the prefix (Native Access installs them
    with a controller's driver or Komplete Kontrol)."""
    return [s for s in NI_SERVICES if p.reg_query(f"{SERVICES_KEY}\\{s}").get("ImagePath")]


def registered(p: Prefix) -> bool: return bool(p.reg_query(f"{SERVICES_KEY}\\{NAME}").get("ImagePath"))


def ensure(p: Prefix, reporter=None) -> bool:
    """Install and start the rescan service when NI's hardware services are there.
    Returns whether it is in place."""
    r = null_reporter(reporter)
    if not ni_hardware_services(p): return False         # no NI hardware software: nothing to restart
    src = source(p)
    if src is None:
        r.log("NI controllers plugged in after start are not seen until the NI services restart; "
              "the fix arrives with the next Wine fixes"); return False
    r.step("Watching for NI controllers plugged in later")
    dst = target(p)
    changed = not dst.exists() or not filecmp.cmp(src, dst, shallow=False)
    if changed:
        if registered(p): p.sc("stop", NAME)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    if not registered(p):
        # sc wants "binPath=" and its value as separate tokens (see ntk_daemon)
        p.sc("create", NAME, "binPath=", r"C:\windows\system32\nirescan.exe", "start=", "auto")
        p.sc("description", NAME, "Restarts Native Instruments' hardware services when an NI controller is plugged in (vstenv)")
    p.reg_add(f"{SERVICES_KEY}\\{NAME}", "Start", "2", "REG_DWORD")
    p.sc("start", NAME)
    ok = registered(p)
    if ok: r.ok("on")
    else: r.fail("could not register the service")
    return ok


def remove(p: Prefix) -> None:
    if registered(p):
        p.sc("stop", NAME); p.sc("delete", NAME)
    target(p).unlink(missing_ok=True)
