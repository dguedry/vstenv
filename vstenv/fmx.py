"""FireMonkey programs: Wine's per-program overrides so they draw with GDI+.

Delphi's FireMonkey (FMX) framework draws a program's whole window itself.
On Windows it picks a canvas at startup: Direct2D on Direct3D 11 when a device
can be created, else its GDI+ software canvas. Under Wine with DXVK the
Direct3D path comes up, and the result is a window whose controls vanish as
the pointer moves over them and dialogs that stay black (Rayzoon's Jamstix 4
Manager, 2026-11-04). With d3d11 and dxgi disabled for that one exe, FMX's
device creation fails as designed and it takes the GDI+ canvas, which draws
every screen correctly; d3d9 stays, since such a program may import it
directly and would not start without it (exit 53, STATUS_DLL_NOT_FOUND).

An FMX program is recognised by its unit names in the file (every FMX build
carries FMX.Platform.Win and the GDI+ canvas unit). The override is applied
when the app runs an installer (the Manager is run that way, never installed)
and when it starts an installed program, so it lands before the first window.
"""
from __future__ import annotations

from pathlib import Path

from .progress import null_reporter
from .wine import Prefix

MARKERS = (b"FMX.Platform.Win", b"FMX.Canvas.GDIP")
DISABLED = ("d3d11", "dxgi")
WHY = "a FireMonkey program: its Direct3D canvas draws wrongly under Wine, its GDI+ one does not"


def is_fmx(exe: Path) -> bool:
    try: b = Path(exe).read_bytes()
    except OSError: return False
    return all(m in b for m in MARKERS)


def key(exe_name: str) -> str:
    return rf"HKCU\Software\Wine\AppDefaults\{exe_name}\DllOverrides"


def applied(p: Prefix, exe_name: str) -> bool:
    try: cur = p.reg_query(key(exe_name))
    except Exception: return False
    return all(cur.get(d) == "disabled" for d in DISABLED)


def apply(p: Prefix, exe_name: str, reporter=None) -> bool:
    """Disable d3d11 and dxgi for this exe. Returns True if something changed."""
    if applied(p, exe_name): return False
    r = null_reporter(reporter)
    r.step(f"GDI+ drawing for {exe_name} ({WHY})")
    for d in DISABLED: p.reg_add(key(exe_name), d, "disabled")
    r.ok(", ".join(f"{d}=disabled" for d in DISABLED))
    return True


def apply_if_fmx(p: Prefix, exe: Path, reporter=None) -> bool:
    """The override for `exe` when it is a FireMonkey program; False otherwise."""
    exe = Path(exe)
    return is_fmx(exe) and apply(p, exe.name, reporter)
