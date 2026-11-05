"""What to try when an installer fails, and what a failure means.

A failed install used to end in a toast. Now it ends in a list: the things a
person can try, each with one line on when it helps, ordered with the ones
that fit the failure's signature first. A remedy is a small set of settings
for *running the installer* (not for the program it installs -- those are
recipes.py's business): run it unattended with the arguments its installer
technology documents, tell it an older Windows, give it Wine's Direct3D or
none, disable one DLL, or treat the file as the program it is. A remedy
that worked is kept for that installer file (recipes' "installs" kind) and
can be shared like any recipe, so the next person's install just works.

Everything here is a fixed menu: no commands, no free-form arguments.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

from .wine import Prefix

# --- installer technology ---------------------------------------------------------------------
TECH_MARKERS = (
    ("inno", b"Inno Setup"), ("nsis", b"Nullsoft"), ("installbuilder", b"BitRock"), ("installbuilder", b"InstallBuilder"),
    ("installaware", b"InstallAware"), ("qtifw", b"Qt Installer Framework"), ("tauri", b"__TAURI_INTERNALS__"),
    ("electron", b"electron-builder"), ("installshield", b"InstallShield"),
)
TECH_LABEL = {"inno": "Inno Setup", "nsis": "NSIS", "installbuilder": "BitRock InstallBuilder", "installaware": "InstallAware",
              "qtifw": "Qt Installer Framework", "tauri": "a Tauri program (not a setup)", "electron": "electron-builder",
              "installshield": "InstallShield", "msi": "Windows Installer (.msi)", "unknown": "unknown"}
# The documented unattended arguments per technology; none for the rest.
SILENT_ARGS = {
    "inno": ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"],
    "nsis": ["/S"],
    "installbuilder": ["--mode", "unattended", "--unattendedmodeui", "none"],
    "msi": ["/qn", "/norestart"],
    "qtifw": ["--accept-licenses", "--confirm-command", "install"],
}


def technology(f: Path) -> str:
    f = Path(f)
    if f.suffix.lower() == ".msi": return "msi"
    try: b = f.read_bytes()
    except OSError: return "unknown"
    for tech, marker in TECH_MARKERS:
        if marker in b: return tech
    return "unknown"


# --- the menu ------------------------------------------------------------------------------
WINVERS = ("auto", "win7", "win8", "win10", "win11")
DISABLE_DLLS = ("auto", "netprofm")
# The knobs of a remedy, as recipes.py's "installs" kind carries them.
KNOBS = {"silent": ("auto", "on"), "winver": WINVERS, "rendering": ("auto", "dxvk", "wine", "gdi"),
         "disable_dll": DISABLE_DLLS, "as_program": ("auto", "on")}

# (id, label, when to try it, settings). Order is the order shown when nothing
# in the failure points at one in particular.
REMEDIES = (
    ("silent", "Run it unattended",
     "The setup's own screens cannot start under Wine: an \"unknown mode\" error, flickering, or no window at all. "
     "Uses the arguments the installer's maker documents for unattended installs.", {"silent": "on"}),
    ("as_program", "It is the program itself: install it as one",
     "The file is not a setup but the application (it opens its own window, and 'installing' it installs nothing). "
     "It is copied into Program Files and listed like any program.", {"as_program": "on"}),
    ("rendering_wine", "Wine's own Direct3D for the setup",
     "The setup's window is blank, black, or draws once and never updates.", {"rendering": "wine"}),
    ("rendering_gdi", "No Direct3D 11 for the setup",
     "Controls vanish as the pointer moves, or the window is black (FireMonkey setups).", {"rendering": "gdi"}),
    ("winver_win8", "Tell it this is Windows 8",
     "The setup checks for something only a newer Windows has and refuses, or says this Windows is not supported.", {"winver": "win8"}),
    ("winver_win7", "Tell it this is Windows 7",
     "Same as above, for a setup that wants an older Windows still.", {"winver": "win7"}),
    ("disable_netprofm", "Disable Wine's network-list service for the setup",
     "A Qt Installer Framework setup answers every click with \"Unknown exception caught.\" (Roland Cloud Manager's did).",
     {"disable_dll": "netprofm"}),
)


def remedy(rid: str) -> dict:
    return next(r[3] for r in REMEDIES if r[0] == rid)


def label(settings: dict) -> str:
    """The remedies' labels for a settings dict, or the settings themselves."""
    names = [r[1] for r in REMEDIES if all(settings.get(k) == v for k, v in r[3].items())]
    return "; ".join(names) if names else ", ".join(f"{k}={v}" for k, v in settings.items())


def options(tech: str, exit_code: int | None = None, log_text: str = "") -> list[tuple[str, str, str, dict, bool]]:
    """The remedies worth offering for this installer, (id, label, when, settings,
    suggested), the suggested ones first. 'silent' only where the technology
    has documented arguments; 'as_program' first when the file is a program."""
    hints = set(suggest(tech, exit_code, log_text))
    out = []
    for rid, lab, when, settings in REMEDIES:
        if rid == "silent" and tech not in SILENT_ARGS: continue
        out.append((rid, lab, when, settings, rid in hints))
    out.sort(key=lambda o: not o[4])
    return out


# --- reading a failure ----------------------------------------------------------------------
_MISSING_DLL = re.compile(r"Library (\S+?\.dll) \(which is needed by", re.I)


def diagnose(tech: str, exit_code: int | None, log_text: str) -> list[str]:
    """What the exit code and Wine's output say, in plain words. Empty when
    nothing recognisable is there."""
    out = []
    m = _MISSING_DLL.search(log_text or "")
    if m or exit_code in (53, 0xC0000135, -1073741515):
        out.append(f"It could not start: a DLL it needs is missing" + (f" ({m.group(1)})" if m else "") +
                   ". A 32-bit program needs the 32-bit copy; re-running setup installs what the app knows about.")
    if "Unknown exception caught" in (log_text or ""):
        out.append("A Qt Installer Framework setup that cannot ask Wine for network information; the "
                   "\"disable Wine's network-list service\" remedy is the one for this.")
    if tech == "tauri":
        out.append("This file is a Tauri program, not a setup: run as an installer it just opens and exits. "
                   "Install it as a program instead.")
    if "unknown mode" in (log_text or "").lower() or (tech == "installbuilder" and exit_code == 1):
        out.append("An InstallBuilder setup whose screens could not start; unattended mode gets past that.")
    if "cannot be closed" in (log_text or "").lower():
        out.append("An electron-builder setup that was told its program was running: the app's PowerShell "
                   "replacement answers that honestly, so re-running usually works.")
    if exit_code == 1602: out.append("Cancelled by the person running it (exit 1602); nothing to fix.")
    if exit_code is not None and exit_code >= 0x80000000 and exit_code not in (0xC0000135,):
        out.append(f"It crashed (status {exit_code:#x}).")
    return out


def suggest(tech: str, exit_code: int | None, log_text: str) -> list[str]:
    """Remedy ids that fit the failure, best first."""
    out = []
    if tech == "tauri": out.append("as_program")
    if "Unknown exception caught" in (log_text or ""): out.append("disable_netprofm")
    if "unknown mode" in (log_text or "").lower() or (tech == "installbuilder" and exit_code == 1): out.append("silent")
    if exit_code is not None and exit_code >= 0x80000000: out.append("rendering_wine")
    return out


# --- applying a remedy to one installer run ----------------------------------------------------
def winver_key(exe_name: str) -> str:
    return rf"HKCU\Software\Wine\AppDefaults\{exe_name}"


def prepare(p: Prefix, installer: Path, settings: dict, tech: str) -> tuple[list[str], dict, callable]:
    """(extra argv, extra environment, cleanup) for running `installer` with
    `settings`. The environment keeps the prefix's own DLL overrides and adds
    the remedy's; the Windows version is set for the installer's exe name and
    removed again by cleanup()."""
    args, env, undo = [], {}, []
    if settings.get("silent") == "on" and tech in SILENT_ARGS: args += SILENT_ARGS[tech]
    overrides = []
    rend = settings.get("rendering", "auto")
    if rend == "wine": overrides.append("d3d10core,d3d11,dxgi=b")
    elif rend == "gdi": overrides.append("d3d11,dxgi=d")
    elif rend == "dxvk": overrides.append("d3d10core,d3d11,dxgi=n,b")
    dll = settings.get("disable_dll", "auto")
    if dll != "auto": overrides.append(f"{dll}=d")
    if overrides: env["WINEDLLOVERRIDES"] = ";".join([p.dll_overrides(), *overrides])
    ver = settings.get("winver", "auto")
    if ver != "auto":
        key = winver_key(installer.name)
        p.reg_add(key, "Version", ver)
        undo.append(lambda: p.reg_delete_value(key, "Version"))
    def cleanup():
        for u in undo:
            try: u()
            except Exception: pass
    return args, env, cleanup


def install_as_program(p: Prefix, f: Path, reporter=None) -> str:
    """Copy a program's exe into Program Files/<name>/ and register it so the
    Programs page and the menu list it. Returns the Windows path of the exe."""
    from .progress import null_reporter
    r = null_reporter(reporter)
    name = f.stem.replace("_", " ").replace("-", " ").strip() or f.stem
    d = p.drive_c / "Program Files" / name
    d.mkdir(parents=True, exist_ok=True)
    dst = d / f.name
    shutil.copy2(f, dst)
    exe = p.to_win(dst)
    key = rf"HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\vstenv-{re.sub(r'[^A-Za-z0-9]+', '-', name).strip('-').lower()}"
    p.reg_add(key, "DisplayName", name)
    p.reg_add(key, "DisplayIcon", exe)
    p.reg_add(key, "InstallLocation", p.to_win(d))
    p.reg_add(key, "Publisher", "")
    r.step(f"Installing {name} as a program"); r.ok(exe)
    return exe
