"""Fast thread synchronization for Wine: the kernel's ntsync driver.

Wine 11 hands NT waits (events, mutexes, semaphores) to /dev/ntsync when the
device exists; without it every wait is a round trip through wineserver.
Plugins with many worker threads that wait on each other all the time (Kontakt's
AuxProcess pool) then cost CPU and stutter. Our Wine build supports it, and
distribution kernels ship the module (Ubuntu 24.04 HWE, Fedora), but nothing
loads it: the device is created only by `modprobe ntsync`.

A Flatpak app cannot load a kernel module, but it can run a command on the host
(host.py), and the host's pkexec asks for the password with the desktop's own
prompt. One prompt loads the module now and on every boot; nothing happens
without the person agreeing. wineserver opens the device when it starts, so a
session already running keeps the slow path until it restarts.
"""
from __future__ import annotations

from . import host, paths

DEVICE = "/dev/ntsync"
LOAD_FILE = "/etc/modules-load.d/ntsync.conf"
DECLINED = paths.DATA / "ntsync-declined"

# Run as root on the host. 0666 is what upstream gives the device; a kernel that
# creates it 0600 gets a udev rule so it stays usable after a reboot.
ENABLE_SCRIPT = f"""set -e
modprobe ntsync
printf 'ntsync\\n' > {LOAD_FILE}
if [ "$(stat -c %a {DEVICE})" != 666 ]; then
    printf 'KERNEL=="ntsync", MODE="0666"\\n' > /etc/udev/rules.d/70-ntsync.rules
    chmod 0666 {DEVICE}
fi
"""

_PROBE = f"""
if [ -e {DEVICE} ]; then echo device=1; else echo device=0; fi
if [ -r {DEVICE} ] && [ -w {DEVICE} ]; then echo usable=1; else echo usable=0; fi
if modinfo ntsync >/dev/null 2>&1 || grep -qw ntsync /lib/modules/$(uname -r)/modules.builtin 2>/dev/null; then echo module=1; else echo module=0; fi
if grep -qsw ntsync /etc/modules-load.d/*.conf /usr/lib/modules-load.d/*.conf /lib/modules-load.d/*.conf; then echo boot=1; else echo boot=0; fi
if command -v pkexec >/dev/null 2>&1; then echo pkexec=1; else echo pkexec=0; fi
used=0
for p in $(pgrep -x wineserver 2>/dev/null); do
    ls -l /proc/$p/fd 2>/dev/null | grep -q ntsync && used=1
done
echo used=$used
echo nofile=$(ulimit -Hn)
"""

# With ntsync every event, mutex and semaphore a Windows program creates is an
# open file in that program, not an entry in wineserver. Wine raises its own
# limit to the hard limit at start, so the hard limit is the ceiling: on a
# system where it is low, Kontakt and SINE Player ran out ("NtCreateFile Too
# many open files, ulimit -n probably needs to be increased") and crashed (#37).
NOFILE_WANTED = 65536
NOFILE_FIX = ("raise the hard open-file limit, then log out and in: as root, put 'DefaultLimitNOFILE=1024:524288' under [Manager] in "
              "/etc/systemd/system.conf.d/nofile.conf and /etc/systemd/user.conf.d/nofile.conf, and '* hard nofile 524288' in "
              "/etc/security/limits.d/nofile.conf")


def status() -> dict:
    """{device, usable, module, boot, pkexec, used, nofile}: facts about the HOST.
    nofile is the hard limit on open files (None: unlimited or unknown)."""
    out = {k: False for k in ("device", "usable", "module", "boot", "pkexec", "used")}
    out["nofile"] = None
    for line in host.sh(_PROBE, timeout=20).splitlines():
        k, _, v = line.partition("=")
        if k == "nofile": out[k] = int(v) if v.strip().isdigit() else None
        elif k in out: out[k] = v.strip() == "1"
    return out


def nofile_low(s: dict) -> bool:
    """ntsync is usable and the open-file ceiling is one a big plugin can reach."""
    n = s.get("nofile")
    return bool(s.get("usable")) and n is not None and n < NOFILE_WANTED


def summary(s: dict) -> tuple[bool, str]:
    """(fine, one line) for Health and the CLI."""
    if s["usable"] and s["used"]: return True, "on: Wine's waits go through the kernel (ntsync)"
    if s["usable"]: return True, "available: Wine uses it from the next time its session starts"
    if s["device"]: return False, f"{DEVICE} exists but this user cannot open it"
    if s["module"]: return False, "off: the kernel has the ntsync driver but it is not loaded (plugins with many threads, like Kontakt, run slower)"
    return True, "not available: this kernel has no ntsync driver (Linux 6.14 or later has it)"


def can_enable(s: dict) -> bool:
    return s["module"] and not s["usable"]


def enable() -> tuple[bool, str]:
    """Load the driver now and at every boot, through the host's password prompt."""
    s = status()
    if s["usable"] and s["boot"]: return True, "already on"
    if not s["module"]: return False, "this kernel has no ntsync driver"
    if not s["pkexec"]:
        return False, f"no pkexec on this system; run as root: modprobe ntsync && echo ntsync > {LOAD_FILE}"
    try:
        cp = host.run(["pkexec", "sh", "-c", ENABLE_SCRIPT], capture_output=True, text=True, timeout=300)
    except Exception as e:
        return False, f"could not ask for the password: {e}"
    if cp.returncode in (126, 127):
        return False, "not changed: the password prompt was cancelled or refused"
    if cp.returncode != 0:
        return False, (cp.stderr.strip().splitlines() or [f"failed (exit {cp.returncode})"])[-1][:160]
    s = status()
    if not s["usable"]: return False, f"the driver loaded but {DEVICE} is not usable yet"
    return True, ("on" if s["used"] else
                  "on. Close your DAW and any Windows programs; Wine picks it up the next time it starts")


def should_offer() -> bool:
    """Ask once per machine state: the driver is there, unused, and the person
    has not said no."""
    if DECLINED.exists(): return False
    try: return can_enable(status())
    except Exception: return False


def decline():
    DECLINED.parent.mkdir(parents=True, exist_ok=True); DECLINED.write_text("asked once; the person chose not to\n")
