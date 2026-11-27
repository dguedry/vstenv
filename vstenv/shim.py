"""A `vstenv` command for the terminal when the app is the Flatpak.

Flatpak exports one wrapper, named after the app id, and it starts the window: the
command line is `flatpak run --command=vstenv <app id> ...`. Setup writes that as
~/.local/bin/vstenv (where the URL handlers and yabridgectl already are), so the
commands the app and its docs name (`vstenv wineasio enable`) can be typed as they
are. Only from the Flatpak: a source checkout or a pip install has its own command.
A file of that name that is not ours is left alone.
"""
from pathlib import Path
from . import APP_ID, APP_NAME, host
from .progress import null_reporter

BIN = Path.home() / ".local/bin"
PATH = BIN / APP_NAME
MARK = "# vstenv command shim"

def content() -> str:
    return (f"#!/bin/sh\n{MARK}: the command line of the installed Flatpak (written by its setup)\n"
            f'exec flatpak run --command={APP_NAME} {APP_ID} "$@"\n')

def ours(path: Path = None) -> bool:
    path = path or PATH
    try: return path.is_file() and MARK in path.read_text(errors="replace")
    except OSError: return False

def on_path() -> bool:
    """Is ~/.local/bin on the host session's PATH? (The sandbox's PATH is not the host's.)"""
    return str(BIN) in host.sh('printf %s "$PATH"', timeout=10).split(":")

def install(reporter=None) -> bool:
    r = null_reporter(reporter); r.step(f"The {APP_NAME} command for terminals")
    if not host.in_flatpak(): r.skip("not the Flatpak: this install has its own command"); return False
    if PATH.exists() and not ours(): r.skip(f"{PATH} is not ours; left alone"); return False
    try:
        BIN.mkdir(parents=True, exist_ok=True)
        if not PATH.exists() or PATH.read_text() != content(): PATH.write_text(content())
        PATH.chmod(0o755)
    except OSError as e: r.skip(f"could not write {PATH}: {e}"); return False
    full = f"flatpak run --command={APP_NAME} {APP_ID}"
    r.ok(str(PATH) if on_path() else f"{PATH} (~/.local/bin is not on your PATH: add it, or type {full})"); return True

def remove(reporter=None) -> bool:
    r = null_reporter(reporter); r.step(f"The {APP_NAME} command for terminals")
    if not ours(): r.skip("none of ours"); return False
    PATH.unlink(missing_ok=True); r.ok("removed"); return True
