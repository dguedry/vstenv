"""The file manager's right-click "Install with VST Environment".

One entry per file manager, each in its own user-level format, written by setup
and removed with `vstenv menu remove`:

- Dolphin (KDE): a service menu in ~/.local/share/kio/servicemenus (must be
  executable on Plasma 6).
- Nemo (Cinnamon): an action in ~/.local/share/nemo/actions.
- Nautilus (GNOME): a script in ~/.local/share/nautilus/scripts, which Nautilus
  lists under Scripts for every file (it has no other way to add an entry).

Thunar, Caja and the rest still get "Open With → VST Environment" from the app's
desktop entry. A file picked this way goes to the same install funnel as the
Install tab: an installer, a zip of plugins, a loose .vst3/.clap/.dll.

.vst3 and .clap have no type in shared-mime-info (a single-file one is sniffed as
a Windows DLL), so a small mime package names them; file managers can then show
the entry for them.
"""
from __future__ import annotations

import os
from pathlib import Path

from . import APP_ID, host, menu
from .progress import null_reporter

SHARE = Path.home() / ".local/share"
NAME = "Install with VST Environment"
MARK = f"# written by {APP_ID}"
MIME_PKG = SHARE / "mime/packages" / f"{APP_ID}.xml"
DOLPHIN = SHARE / "kio/servicemenus" / f"{APP_ID}-install.desktop"
NEMO = SHARE / "nemo/actions" / f"{APP_ID}-install.nemo_action"
NAUTILUS = SHARE / "nautilus/scripts" / NAME
PLUGIN_TYPES = ["application/x-vst3-plugin", "application/x-clap-plugin"]
TYPES = ["application/x-ms-dos-executable", "application/x-msi", "application/x-msdownload",
         "application/vnd.microsoft.portable-executable", "application/zip", "application/x-7z-compressed",
         "application/vnd.rar", "application/x-rar", "application/x-tar", "application/x-compressed-tar",
         "application/x-xz-compressed-tar", "application/x-bzip-compressed-tar"] + PLUGIN_TYPES
EXTENSIONS = ["exe", "msi", "zip", "7z", "rar", "tar", "tgz", "gz", "xz", "bz2", "vst3", "clap", "dll"]

MIME_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<!-- written by {APP_ID} -->
<mime-info xmlns="http://www.freedesktop.org/standards/shared-mime-info">
  <mime-type type="application/x-vst3-plugin">
    <comment>VST3 plugin</comment>
    <glob pattern="*.vst3" weight="80"/>
  </mime-type>
  <mime-type type="application/x-clap-plugin">
    <comment>CLAP plugin</comment>
    <glob pattern="*.clap" weight="80"/>
  </mime-type>
</mime-info>
"""

def gui_launcher() -> str:
    """The command that opens the app's window with a file to install."""
    l = menu.launcher()
    if l.startswith("flatpak run --command="): return f"flatpak run {APP_ID}"
    if l.split()[0] != "env" and host.which("vstenv-gui"): return "vstenv-gui"
    return l.replace(" -m vstenv", " -m vstenv.gui") if " -m vstenv" in l else l

def dolphin(cmd: str) -> str:
    return (f"{MARK}\n[Desktop Entry]\nType=Service\nMimeType={';'.join(TYPES)};\nActions=install\n"
            f"X-KDE-Priority=TopLevel\nX-KDE-RequiredNumberOfUrls=1\n\n"
            f"[Desktop Action install]\nName={NAME}\nIcon={APP_ID}\nExec={cmd} %f\n")

def nemo(cmd: str) -> str:
    return (f"{MARK}\n[Nemo Action]\nName={NAME}\nComment=Install it into the Windows prefix and bridge its plugins\n"
            f"Exec={cmd} %F\nIcon-Name={APP_ID}\nSelection=s\nExtensions={';'.join(EXTENSIONS)};\n"
            f"Mimetypes={';'.join(TYPES)};\n")

def nautilus(cmd: str) -> str:
    return f'#!/bin/sh\n{MARK}\n# Nautilus passes the selected files as arguments; the first one is installed.\nexec {cmd} "$1"\n'

def _entries() -> list[tuple[Path, callable, bool]]:
    """(path, content(cmd), executable) for each file manager entry."""
    return [(DOLPHIN, dolphin, True), (NEMO, nemo, False), (NAUTILUS, nautilus, True)]

def _ours(p: Path) -> bool:
    try: return MARK in p.read_text(errors="replace")[:400]
    except OSError: return False

def apply(reporter=None) -> int:
    """Write (or refresh) every entry; returns how many changed. A file the user
    put at one of these paths without our mark is left alone."""
    r = null_reporter(reporter); r.step("File manager: right-click “Install with VST Environment”")
    cmd = gui_launcher(); changed = 0
    for path, content, exe in _entries():
        want = content(cmd)
        if path.exists() and not _ours(path): continue
        try:
            if not path.exists() or path.read_text(errors="replace") != want:
                path.parent.mkdir(parents=True, exist_ok=True); path.write_text(want); changed += 1
            if exe: os.chmod(path, 0o755)
        except OSError: pass
    if not MIME_PKG.exists() or MIME_PKG.read_text(errors="replace") != MIME_XML:
        try:
            MIME_PKG.parent.mkdir(parents=True, exist_ok=True); MIME_PKG.write_text(MIME_XML); changed += 1
            host.desktop_tool(["update-mime-database", str(SHARE / "mime")], timeout=120)
        except OSError: pass
    r.ok("Dolphin, Nemo, Nautilus (Scripts)" + (" updated" if changed else ""))
    return changed

def remove(reporter=None) -> int:
    r = null_reporter(reporter); r.step("Removing the file manager entries"); n = 0
    for path, _c, _e in _entries():
        if path.exists() and _ours(path):
            try: path.unlink(); n += 1
            except OSError: pass
    if MIME_PKG.exists() and MARK.lstrip("# ") in MIME_PKG.read_text(errors="replace"):
        MIME_PKG.unlink(); n += 1
        host.desktop_tool(["update-mime-database", str(SHARE / "mime")], timeout=120)
    r.ok(f"{n} removed" if n else "none")
    return n

def status() -> dict:
    return {str(p): p.exists() and _ours(p) for p, _c, _e in _entries()} | {str(MIME_PKG): MIME_PKG.exists()}
