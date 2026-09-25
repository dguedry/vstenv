"""Wine Mono: the .NET runtime for programs that need one.

The prefix disables mscoree by default so that wineboot never asks about
installing Mono and .NET-free vendors are not slowed down by it. Steinberg's
Install Assistant and Install Helper are .NET (IL-only) executables that the
Download Assistant runs for every runtime component and product install, and
they run correctly under Wine Mono (2026-09-25: Library Manager, Activation
Manager and the built-in ASIO driver installed through them). So Mono is
installed on demand, into the prefix, from WineHQ's build matching this Wine,
and wine_env() stops disabling mscoree once it is there.
"""
from __future__ import annotations

import re
from pathlib import Path

from . import paths
from .download import fetch
from .progress import null_reporter
from .wine import Prefix, WineBuild

FALLBACK_VERSION = "11.3.0"
URL = "https://dl.winehq.org/wine/wine-mono/{v}/wine-mono-{v}-x86.msi"

def required_version(build: WineBuild) -> str:
    """The Wine Mono version this Wine's mscoree asks for (an ASCII x.y.z string
    in mscoree.dll, next to its Mono search paths)."""
    for dll in (build.root / "lib/wine/x86_64-windows/mscoree.dll", build.root / "lib/wine/i386-windows/mscoree.dll"):
        try: b = dll.read_bytes()
        except OSError: continue
        i = b.find(b"\\mono")
        window = b[max(0, i - 4096): i + 4096] if i >= 0 else b
        m = re.findall(rb"(?<![0-9.])(\d{1,2}\.\d{1,2}\.\d{1,2})(?![0-9.])", window)
        vs = [v.decode() for v in m if v.split(b".")[0].isdigit() and 4 <= int(v.split(b".")[0]) <= 30]
        if vs: return vs[0]
    return FALLBACK_VERSION

def installed(p: Prefix) -> bool:
    d = p.drive_c / "windows/mono"
    return d.is_dir() and any(x.is_dir() for x in d.iterdir())

def install(p: Prefix, reporter=None) -> bool:
    """Returns True if Mono was installed now."""
    r = null_reporter(reporter)
    r.step("Installing Wine Mono (.NET runtime)")
    if installed(p): r.skip("already"); return False
    v = required_version(p.build)
    msi = fetch(URL.format(v=v), paths.DOWNLOADS / f"wine-mono-{v}-x86.msi", reporter=r, label=f"Wine Mono {v}")
    cp = p.run(["msiexec", "/i", str(msi), "/qn"], timeout=1800,
               env={"WINEDLLOVERRIDES": p.dll_overrides(mono=True)})
    if cp.returncode != 0 or not installed(p):
        r.fail(f"msiexec exit {cp.returncode}"); return False
    r.ok(v); return True

def status(p: Prefix) -> dict:
    return {"installed": installed(p), "required": required_version(p.build)}
