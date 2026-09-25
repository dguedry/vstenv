"""XDG-style locations. Everything the app owns lives under these.

Inside the Flatpak the XDG_* variables point into ~/.var/app/<id>/, which
would give the sandboxed app an environment of its own. That is the wrong
split here: Wine, the vendor managers and the DAW-side plugin hosts all run on
the host (host.py), the sandbox sees the whole home directory, and a machine
with two prefixes has two registries, two wineservers and one vendor daemon
that only one of them can own (prefixes.py). So the Flatpak uses the same
host locations a native install uses, and either entry point finds what the
other set up.
"""
import os
from pathlib import Path

APP = "vstenv"
_IN_FLATPAK = Path("/.flatpak-info").exists()

def _xdg(var, default):
    if not _IN_FLATPAK and os.environ.get(var): return Path(os.environ[var])
    return Path.home() / default

DATA = _xdg("XDG_DATA_HOME", ".local/share") / APP
CACHE = _xdg("XDG_CACHE_HOME", ".cache") / APP
CONFIG = _xdg("XDG_CONFIG_HOME", ".config") / APP
DOWNLOADS = CACHE / "downloads"
WINE_DIR = DATA / "wine"          # one subdir per provisioned wine build
PREFIX = DATA / "prefix"          # the default prefix
LOGS = DATA / "logs"

def ensure_dirs():
    for d in (DATA, CACHE, CONFIG, DOWNLOADS, WINE_DIR, LOGS):
        d.mkdir(parents=True, exist_ok=True)
