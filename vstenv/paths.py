"""XDG-style locations. Everything the app owns lives under these."""
import os
from pathlib import Path

APP = "vstenv"

def _xdg(var, default):
    return Path(os.environ.get(var) or (Path.home() / default))

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
