"""Plasma's or GNOME's window colours for Wine's native menu bars (opt-in: recipe setting desktop_menu_colors).

Wine draws a program's native menu bar (Cubase's File/Edit/...) from HKCU\\Control Panel\\Colors
Menu, MenuBar and MenuText, white by default, so the bar stays white on a dark Plasma theme.
With the setting on, Menu and MenuBar take [Colors:Window] BackgroundNormal of kdeglobals and
MenuText its ForegroundNormal. On GNOME (gsettings, asked on the host) the light or dark
libadwaita window colours stand in. XDG_CURRENT_DESKTOP picks which source goes first. Nothing is written when the file or a key is missing. The last
applied colours are kept in the prefix (drive_c/vstenv-menu-colors.json) so a launch costs no Wine
run when they have not changed.
"""
import json, os
from pathlib import Path
from . import host, paths
from .wine import Prefix

KEY = r"HKCU\Control Panel\Colors"
STATE = "vstenv-menu-colors.json"
GNOME_FAMILY = ("gnome", "budgie", "pantheon", "unity", "cinnamon")   # desktops that expose org.gnome.desktop.interface
DARK, LIGHT = ((36, 36, 36), (255, 255, 255)), ((250, 250, 250), (51, 51, 51))   # libadwaita window bg / fg

def _palette(bg_fg) -> dict[str, str]:
    bg, fg = (" ".join(map(str, c)) for c in bg_fg)
    return {"Menu": bg, "MenuBar": bg, "MenuText": fg}

def _gsetting(key: str) -> str | None:
    cp = host.desktop_tool(["gsettings", "get", "org.gnome.desktop.interface", key], timeout=3)
    return cp.stdout.strip().strip("'\"") if cp is not None and cp.returncode == 0 else None

def gnome_colors() -> dict[str, str] | None:
    """The dark or light palette for color-scheme, else for a gtk-theme named *-dark; None when unknown."""
    scheme = _gsetting("color-scheme")
    if scheme == "prefer-dark": return _palette(DARK)
    if scheme in ("default", "prefer-light"): return _palette(LIGHT)
    if scheme is None:
        theme = _gsetting("gtk-theme")
        if theme and theme.lower().endswith("-dark"): return _palette(DARK)
    return None

def desktop_colors(kdeglobals: Path | None = None) -> dict[str, str] | None:
    """GNOME-family sessions ask gsettings first and fall back to kdeglobals; everything else the other way round."""
    gnome = any(d in os.environ.get("XDG_CURRENT_DESKTOP", "").lower() for d in GNOME_FAMILY)
    sources = (gnome_colors, lambda: plasma_colors(kdeglobals)) if gnome else (lambda: plasma_colors(kdeglobals), gnome_colors)
    return sources[0]() or sources[1]()

def _rgb(s) -> str | None:
    """'61,174,233' -> '61 174 233' (the registry's form); None when it is not three 0-255 numbers."""
    try: parts = [int(x) for x in str(s).split(",")]
    except ValueError: return None
    return " ".join(map(str, parts)) if len(parts) == 3 and all(0 <= x <= 255 for x in parts) else None

def plasma_colors(path: Path | None = None) -> dict[str, str] | None:
    """{value name: 'r g b'} from kdeglobals, or None when the file or either key is missing or malformed."""
    path = path or paths._xdg("XDG_CONFIG_HOME", ".config") / "kdeglobals"   # in the Flatpak: the host's ~/.config (--filesystem=host), not the remapped XDG_CONFIG_HOME
    try: lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError: return None
    sec, got = None, {}
    for line in lines:
        line = line.strip()
        if line.startswith("["): sec = line
        elif sec == "[Colors:Window]" and "=" in line:
            k, v = line.split("=", 1); got[k.strip()] = v.strip()
    bg, fg = _rgb(got.get("BackgroundNormal")), _rgb(got.get("ForegroundNormal"))
    return {"Menu": bg, "MenuBar": bg, "MenuText": fg} if bg and fg else None

def apply(p: Prefix, kdeglobals: Path | None = None) -> bool:
    """Write the colours when they differ from the last applied ones. True when Wine was run."""
    want = desktop_colors(kdeglobals)
    if not want: return False
    state = p.drive_c / STATE
    try: have = json.loads(state.read_text())
    except (OSError, ValueError): have = None
    if have == want: return False
    for k, v in want.items():
        cp = p.run(["reg", "add", KEY, "/v", k, "/t", "REG_SZ", "/d", v, "/f"], timeout=60)
        if cp.returncode != 0: return False          # not recorded, so the next start tries again
    state.write_text(json.dumps(want)); return True

def reset(p: Prefix) -> bool:
    """Setting turned off: delete the three values so Wine's built-in colours apply again.
    Only when apply() wrote them (its state file). True when Wine was run."""
    state = p.drive_c / STATE
    if not state.exists(): return False
    for k in ("Menu", "MenuBar", "MenuText"): p.run(["reg", "delete", KEY, "/v", k, "/f"], timeout=60)   # already gone: nothing to undo
    state.unlink(); return True
