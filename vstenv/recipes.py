"""Recipes: the few settings a program or plugin can be given, who chose them,
and how a choice that worked gets shared.

Most of the per-program fixes in this app are the same handful of knobs turned
one way or another, all keyed by the program's exe name in Wine's registry:
which Direct3D it gets (DXVK, Wine's own, or none so the framework falls back
to plain Windows drawing), whether the window manager is kept away from its
windows, and, for a program whose window is an embedded browser, whether that
browser draws in software. The app *detects* what a program needs (imports
dcomp.dll -> Wine's Direct3D; FireMonkey unit names -> no Direct3D 11; a
WebView2 host -> browser flags). A recipe is the same knob set written down,
so that a person can try the other settings when detection got it wrong, keep
the one that worked, and send it in.

Three layers, the first that speaks wins:

1. the user's own choices for this prefix (drive_c/vstenv-recipes.json);
2. the recipe table: the copy shipped with the app, overlaid by a newer copy
   fetched from the repository at setup, so a merged recipe reaches people
   without a release;
3. detection ("auto"), which is what every program gets until someone says
   otherwise.

A recipe can only set values from the fixed menus below -- no commands, no
paths -- so the worst a bad one can do is make one program draw wrong until
it is reset. Plugins have no exe of their own (they run inside yabridge's
host), so their one knob, rendering, travels as a DLL override the host
launcher applies when the plugin it loads matches by name.
"""
from __future__ import annotations

import json
import os
import platform
import urllib.parse
from pathlib import Path

from . import paths
from .progress import null_reporter
from .wine import Prefix

RENDERING = ("auto", "dxvk", "wine", "gdi")
TRISTATE = ("auto", "on", "off")
KNOBS = {"programs": {"rendering": RENDERING, "wm_away": TRISTATE, "browser_software_gl": TRISTATE},
         "plugins": {"rendering": RENDERING}}
KINDS = tuple(KNOBS)

# What each option means, in the words the UI and the CLI use.
RENDERING_TEXT = {
    "auto": "What the app detected: DXVK, unless the program draws through DirectComposition (then Wine's Direct3D) or is a FireMonkey program (then no Direct3D 11).",
    "dxvk": "Direct3D on Vulkan, the default. Right for nearly everything.",
    "wine": "Wine's own Direct3D. Try it when the window is blank or draws once and never updates.",
    "gdi": "No Direct3D 11. The program falls back to plain Windows drawing if it can (FireMonkey can; JUCE 8 cannot). Try it when controls vanish as the pointer moves.",
}
KNOB_TEXT = {
    "rendering": "Which Direct3D the program gets",
    "wm_away": "Keep the window manager away (menus that flash and close as soon as they open)",
    "browser_software_gl": "Software rendering for its embedded browser window (a window that stays blank or black)",
}
UNNECESSARY = "Most programs need none of this. Only try a setting when you have the problem it describes."

CHOICES = "vstenv-recipes.json"                     # in drive_c: this prefix's own choices
SHIPPED = Path(__file__).with_name("recipes.json")  # the table that comes with the app
FETCHED = paths.DATA / "recipes.json"               # a newer table from the repository
TABLE_URL = "https://raw.githubusercontent.com/dguedry/vstenv/main/vstenv/recipes.json"
ISSUES_URL = "https://github.com/dguedry/vstenv/issues/new"

# Registry values each rendering choice means for a program's exe.
_D3D = ("d3d10core", "d3d11", "dxgi")
_PROGRAM_RENDERING = {
    "dxvk": {d: "native,builtin" for d in _D3D},
    "wine": {d: "builtin" for d in _D3D},
    "gdi": {"d3d10core": "builtin", "d3d11": "disabled", "dxgi": "disabled"},
}
# ...and the WINEDLLOVERRIDES a plugin's host gets, by name match.
_PLUGIN_RENDERING = {"dxvk": "d3d10core,d3d11,dxgi=n,b", "wine": "d3d10core,d3d11,dxgi=b", "gdi": "d3d11,dxgi=d"}


# --- the tables ---------------------------------------------------------------------------
def _clean(raw) -> dict:
    """Only known kinds, knobs and values survive; an 'auto' is the same as
    nothing. Free-text fields a table may carry (why, confirmed) are kept."""
    out = {k: {} for k in KINDS}
    if not isinstance(raw, dict): return out
    for kind in KINDS:
        entries = raw.get(kind)
        if not isinstance(entries, dict): continue
        for name, settings in entries.items():
            if not isinstance(name, str) or not isinstance(settings, dict): continue
            keep = {}
            for knob, allowed in KNOBS[kind].items():
                v = settings.get(knob)
                if isinstance(v, str) and v in allowed and v != "auto": keep[knob] = v
            if keep:
                for extra in ("why", "confirmed"):
                    if extra in settings: keep[extra] = settings[extra]
                out[kind][name] = keep
    return out


def _read(path: Path) -> dict:
    try: return _clean(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError): return _clean({})


def table() -> dict:
    """The recipe table: the shipped copy, with the fetched one laid over it."""
    out = _read(SHIPPED)
    fetched = _read(FETCHED)
    for kind in KINDS: out[kind].update(fetched[kind])
    return out


def update(reporter=None, timeout=10) -> bool:
    """A newer table from the repository, kept beside the app's data. Quiet on
    any failure: the shipped copy is always there."""
    from .download import text
    r = null_reporter(reporter)
    try:
        body = text(TABLE_URL, timeout=timeout)
        cleaned = _clean(json.loads(body))
        FETCHED.parent.mkdir(parents=True, exist_ok=True)
        FETCHED.write_text(json.dumps(cleaned, indent=1))
        n = sum(len(cleaned[k]) for k in KINDS)
        r.step("Recipe table"); r.ok(f"{n} from the repository")
        return True
    except Exception as e:
        r.step("Recipe table"); r.skip(f"shipped copy ({str(e)[:60]})")
        return False


# --- the user's choices ---------------------------------------------------------------------
def choices(p: Prefix) -> dict:
    return _read(p.drive_c / CHOICES)


def _write_choices(p: Prefix, data: dict):
    (p.drive_c / CHOICES).write_text(json.dumps({k: data[k] for k in KINDS}, indent=1))


def set_choice(p: Prefix, kind: str, name: str, **settings) -> dict:
    """Record settings for one program or plugin ('auto' for a knob forgets it).
    Returns what is recorded for it afterwards."""
    if kind not in KINDS: raise ValueError(f"no such kind: {kind}")
    for knob, v in settings.items():
        if knob not in KNOBS[kind]: raise ValueError(f"{kind} have no setting '{knob}'")
        if v not in KNOBS[kind][knob]: raise ValueError(f"{knob} can be {', '.join(KNOBS[kind][knob])}, not '{v}'")
    data = choices(p)
    cur = dict(data[kind].get(name, {}))
    for knob, v in settings.items():
        if v == "auto": cur.pop(knob, None)
        else: cur[knob] = v
    if cur: data[kind][name] = cur
    else: data[kind].pop(name, None)
    _write_choices(p, data)
    return cur


def reset(p: Prefix, kind: str, name: str) -> bool:
    data = choices(p)
    if name not in data[kind]: return False
    del data[kind][name]; _write_choices(p, data); return True


def pinned(p: Prefix, kind: str, name: str, knob: str) -> bool:
    """A knob the user set, or a recipe sets: detection must leave it alone."""
    return knob in choices(p)[kind].get(name, {}) or knob in table()[kind].get(name, {})


def effective(p: Prefix, kind: str, name: str) -> dict[str, tuple[str, str]]:
    """{knob: (value, who)}: who is 'you' (a choice), 'recipe' (the table) or
    'auto' (detection), for every knob of this kind."""
    mine = choices(p)[kind].get(name, {}); theirs = table()[kind].get(name, {})
    out = {}
    for knob in KNOBS[kind]:
        if knob in mine: out[knob] = (mine[knob], "you")
        elif knob in theirs: out[knob] = (theirs[knob], "recipe")
        else: out[knob] = ("auto", "auto")
    return out


# --- applying ---------------------------------------------------------------------------------
def overrides_key(exe_name: str) -> str:
    return rf"HKCU\Software\Wine\AppDefaults\{exe_name}\DllOverrides"


def apply_program(p: Prefix, exe_name: str, settings: dict, reporter=None, exe_path: Path | None = None) -> list[str]:
    """Write a program's settings to the registry. 'auto' for rendering removes
    the per-exe Direct3D values and lets detection put back what it finds, when
    the exe is at hand. Returns what was changed."""
    from . import unmanaged, webview2, dcomp, fmx
    r = null_reporter(reporter); done = []
    rend = settings.get("rendering", "auto")
    cur = {}
    try: cur = p.reg_query(overrides_key(exe_name))
    except Exception: pass
    if rend != "auto":
        want = _PROGRAM_RENDERING[rend]
        if any(cur.get(d) != v for d, v in want.items()):
            for d, v in want.items(): p.reg_add(overrides_key(exe_name), d, v)
            done.append(f"rendering={rend}")
    elif any(d in cur for d in _D3D):
        for d in _D3D:
            if d in cur: p.reg_delete_value(overrides_key(exe_name), d)
        done.append("rendering=auto")
        if exe_path is not None and exe_path.exists():
            if fmx.is_fmx(exe_path): fmx.apply(p, exe_name)
            elif dcomp.imports_dcomp(exe_path): dcomp.apply_program_overrides(p, [exe_name])
    wm = settings.get("wm_away", "auto")
    if wm == "on" and not unmanaged.applied(p, exe_name): unmanaged.apply(p, [exe_name]); done.append("wm_away=on")
    elif wm == "off" and unmanaged.applied(p, exe_name): unmanaged.clear(p, exe_name); done.append("wm_away=off")
    gl = settings.get("browser_software_gl", "auto")
    if gl != "auto":
        flags = webview2.PRESENTATION_FLAGS + (" " + webview2.SOFTWARE_GL_FLAGS if gl == "on" else "")
        have = {}
        try: have = p.reg_query(webview2.POLICY_KEY)
        except Exception: pass
        if have.get(exe_name) != flags:
            p.reg_add(webview2.POLICY_KEY, exe_name, flags); done.append(f"browser_software_gl={gl}")
    if done:
        r.step(f"Recipe for {exe_name}"); r.ok(", ".join(done))
    return done


def apply_all(p: Prefix, reporter=None) -> list[str]:
    """Every program with a choice or a table recipe, at setup. Plugins go
    through plugin_overrides() into the host launcher's file instead."""
    r = null_reporter(reporter); done = []
    mine, theirs = choices(p)["programs"], table()["programs"]
    for exe_name in sorted(set(mine) | set(theirs)):
        settings = dict(theirs.get(exe_name, {})); settings.update(mine.get(exe_name, {}))
        settings = {k: v for k, v in settings.items() if k in KNOBS["programs"]}
        try:
            for what in apply_program(p, exe_name, settings, r): done.append(f"{exe_name}: {what}")
        except Exception as e:
            r.step(f"Recipe for {exe_name}"); r.fail(str(e)[:80])
    return done


def plugin_overrides(p: Prefix) -> list[tuple[str, str]]:
    """(name pattern, WINEDLLOVERRIDES) for every plugin with a rendering
    choice or recipe, for the host launcher's file."""
    mine, theirs = choices(p)["plugins"], table()["plugins"]
    out = []
    for name in sorted(set(mine) | set(theirs)):
        rend = mine.get(name, {}).get("rendering") or theirs.get(name, {}).get("rendering")
        if rend and rend != "auto": out.append((name, _PLUGIN_RENDERING[rend]))
    return out


def in_effect(p: Prefix) -> list[str]:
    """One line per program or plugin with a setting, for Health."""
    out = []
    for kind in KINDS:
        mine, theirs = choices(p)[kind], table()[kind]
        for name in sorted(set(mine) | set(theirs)):
            parts = []
            for knob, (v, who) in effective(p, kind, name).items():
                if who != "auto": parts.append(f"{knob}={v} ({'yours' if who == 'you' else 'recipe'})")
            if parts: out.append(f"{name}: {', '.join(parts)}")
    return out


# --- sharing ------------------------------------------------------------------------------
def environment(p: Prefix | None = None) -> dict[str, str]:
    """The facts a recipe needs beside it to be worth anything to someone else."""
    from . import __version__, dxvk, wine
    out = {"vstenv": __version__}
    try:
        b = p.build if p is not None else wine.installed_build()
        out["wine"] = b.root.name if b is not None else "?"
    except Exception: out["wine"] = "?"
    try: out["dxvk"] = (dxvk.installed_version(p) if p is not None else None) or "not installed"
    except Exception: out["dxvk"] = "?"
    try: out["gpu"] = dxvk.vulkan_ok()[1]
    except Exception: out["gpu"] = "?"
    out["session"] = f"{os.environ.get('XDG_SESSION_TYPE', '?')} / {os.environ.get('XDG_CURRENT_DESKTOP', '?')}"
    try:
        osr = dict(l.split("=", 1) for l in Path("/etc/os-release").read_text(errors="replace").splitlines() if "=" in l)
        out["distribution"] = osr.get("PRETTY_NAME", "?").strip('"')
    except OSError: out["distribution"] = platform.platform()
    return out


def share_text(p: Prefix | None, kind: str, name: str, settings: dict, symptom: str = "",
               program_version: str | None = None, worked: bool = True) -> tuple[str, str]:
    """(title, body) for an issue: the recipe as a JSON block the table takes
    as is, the symptom in the person's words, and the environment."""
    kept = {k: v for k, v in settings.items() if k in KNOBS[kind] and v != "auto"}
    entry = dict(kept)
    if symptom: entry["why"] = symptom
    env = environment(p)
    entry["confirmed"] = [f"{env['gpu']}; {env['session']}; vstenv {env['vstenv']}"]
    title = f"Recipe: {name}" + (f" ({program_version})" if program_version else "") + ("" if worked else " — did not help")
    lines = [f"**{'Worked' if worked else 'Did not help'}:** " + (", ".join(f"{k}={v}" for k, v in kept.items()) or "nothing changed"),
             "", f"**Symptom:** {symptom or '(please describe what you saw)'}", "",
             "```json", json.dumps({kind: {name: entry}}, indent=1), "```", "",
             "**Environment**"] + [f"- {k}: {v}" for k, v in env.items()]
    return title, "\n".join(lines)


def share_url(p: Prefix | None, kind: str, name: str, settings: dict, symptom: str = "",
              program_version: str | None = None, worked: bool = True) -> str:
    """A link that opens a prefilled issue on the repository: nothing is sent
    until the person presses Submit there."""
    title, body = share_text(p, kind, name, settings, symptom, program_version, worked)
    q = urllib.parse.urlencode({"title": title, "body": body, "labels": "recipe"})
    return f"{ISSUES_URL}?{q}"
