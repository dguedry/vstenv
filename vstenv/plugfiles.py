"""Plugins that come without an installer: a .zip (or .7z, .rar, .tar.*) holding
.vst3, .clap or VST2 .dll files, or such a file picked on its own.

The plugins are copied where Windows hosts look for them -- VST3 to Common
Files/VST3, CLAP to Common Files/CLAP, VST2 to Program Files/VstPlugins -- and
the usual environment pass bridges them. An archive with no plugins but an
installer in it hands that installer back to the install funnel, so a zipped
setup.exe needs no unzipping first.

What a download carries for other systems is left out: macOS bundles and
.component files, AAX, and 32-bit builds (the bundled yabridge has no 32-bit
host, so those could not be bridged). Each is named in the result.
"""
from __future__ import annotations

import shutil
import subprocess
import tarfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from . import paths, pe

ARCHIVES = (".zip", ".7z", ".rar", ".tar", ".tgz", ".tar.gz", ".tar.xz", ".tar.bz2", ".txz")
PLUGIN_SUFFIXES = (".vst3", ".clap", ".dll")
DEST = {"VST3": "Program Files/Common Files/VST3",
        "CLAP": "Program Files/Common Files/CLAP",
        "VST2": "Program Files/VstPlugins"}
INSTALLERS = (".exe", ".msi")
# Files beside a VST2 .dll that are only reading matter: a folder holding the
# plugin and nothing but these is copied as the bare .dll.
DOCS = (".txt", ".pdf", ".rtf", ".md", ".url", ".html", ".htm", ".webloc", ".png", ".jpg")
JUNK = ("__MACOSX", ".DS_Store", "Thumbs.db", "desktop.ini")


@dataclass
class Found:
    fmt: str                 # VST3, CLAP, VST2
    src: Path                # the file, or a VST3 bundle folder
    arch: str | None         # x64, x86, arm64, ...

@dataclass
class Result:
    installed: list[tuple[str, Path]] = field(default_factory=list)   # (format, destination)
    skipped: list[tuple[str, str]] = field(default_factory=list)      # (name, why)
    installers: list[Path] = field(default_factory=list)              # setups found instead of plugins


def _suffix(p: Path) -> str:
    n = p.name.lower()
    for s in ARCHIVES:
        if n.endswith(s): return s
    return p.suffix.lower()

def is_archive(p: Path) -> bool:
    return Path(p).is_file() and _suffix(Path(p)) in ARCHIVES

def handles(p: Path) -> bool:
    """Whether this module takes the file: an archive, a plugin file, or a folder
    (a VST3 bundle, or an unpacked download) holding at least one plugin."""
    p = Path(p)
    if p.is_dir(): return bool(scan(p)[0]) or p.suffix.lower() == ".vst3"
    if is_archive(p): return True
    s = p.suffix.lower()
    if s in (".vst3", ".clap"): return True
    if s == ".dll": return _vst2(p)
    return False


def _vst2(f: Path) -> bool:
    ex = pe.exports(f)
    return "VSTPluginMain" in ex or "main" in ex

def _bundle_binary(b: Path) -> tuple[Path | None, str | None, str]:
    """(binary, arch, why-not) for a .vst3 bundle folder: the 64-bit Windows
    binary when there is one."""
    c = b / "Contents"
    for sub, arch in (("x86_64-win", "x64"), ("arm64x-win", "x64"), ("x86-win", "x86"), ("arm64-win", "arm64"), ("arm64ec-win", "arm64")):
        d = c / sub
        if d.is_dir():
            bins = [x for x in d.iterdir() if x.suffix.lower() == ".vst3" and x.is_file()]
            if bins: return bins[0], pe.machine(bins[0]) or arch, ""
    if (c / "MacOS").is_dir() or (c / "Info.plist").exists(): return None, None, "macOS build"
    return None, None, "no Windows binary inside"

def _junk(p: Path) -> bool:
    return any(part in JUNK or part.startswith("._") for part in p.parts)

def scan(root: Path) -> tuple[list[Found], list[tuple[str, str]], list[Path]]:
    """(plugins, skipped, installers) under a folder; `root` itself may be a bundle."""
    root = Path(root); found: list[Found] = []; skipped: list[tuple[str, str]] = []; installers: list[Path] = []
    def visit(d: Path):
        if d.suffix.lower() == ".vst3" and d.is_dir():
            bin_, arch, why = _bundle_binary(d)
            if bin_ is not None: found.append(Found("VST3", d, arch))
            else: skipped.append((d.name, why))
            return
        if d.suffix.lower() in (".component", ".aaxplugin", ".vst", ".app", ".bundle", ".lv2") and d.is_dir():
            skipped.append((d.name, {".aaxplugin": "AAX (Pro Tools)", ".lv2": "LV2 (not a Windows format)"}.get(d.suffix.lower(), "macOS build"))); return
        for x in sorted(d.iterdir()):
            if _junk(x.relative_to(root)): continue
            if x.is_dir(): visit(x); continue
            s = x.suffix.lower()
            if s == ".vst3": found.append(Found("VST3", x, pe.machine(x)))
            elif s == ".clap": found.append(Found("CLAP", x, pe.machine(x)))
            elif s == ".dll" and _vst2(x): found.append(Found("VST2", x, pe.machine(x)))
            elif s == ".aaxplugin": skipped.append((x.name, "AAX (Pro Tools)"))
            elif s in INSTALLERS and not x.name.lower().startswith("unins"): installers.append(x)
    if root.is_dir(): visit(root)
    elif root.is_file():
        s = root.suffix.lower()
        if s in (".vst3", ".clap"): found.append(Found(s[1:].upper(), root, pe.machine(root)))
        elif s == ".dll" and _vst2(root): found.append(Found("VST2", root, pe.machine(root)))
    # Keep the 64-bit build of each plugin. A plugin with only other builds is
    # reported, not copied: it could not be bridged.
    keep: list[Found] = []
    for f in found:
        if f.arch == "x64": keep.append(f); continue
        twin = any(g.arch == "x64" and g.fmt == f.fmt and _stem(g.src) == _stem(f.src) for g in found)
        if not twin:
            skipped.append((f.src.name, "macOS build" if f.arch is None and f.fmt != "VST2" else
                            "32-bit build (only 64-bit plugins can be bridged)" if f.arch == "x86" else f"{f.arch or 'unknown'} build"))
    # A dll that is the binary of a kept bundle is not a second plugin.
    return keep, skipped, installers

def _stem(p: Path) -> str:
    return p.name.lower().rsplit(".", 1)[0].replace("(64 bit)", "").replace("x64", "").replace("_64", "").strip(" _-")


def unpack(archive: Path, dest: Path, reporter=None) -> Path:
    """Unpack into `dest` (emptied first); zip and tar need nothing else, 7z and
    rar go through the app's 7-Zip."""
    archive = Path(archive); dest = Path(dest)
    if dest.exists(): shutil.rmtree(dest)
    dest.mkdir(parents=True)
    s = _suffix(archive)
    if s == ".zip":
        with zipfile.ZipFile(archive) as z: z.extractall(dest)      # drops absolute and .. member paths
    elif s.startswith(".t"):
        with tarfile.open(archive) as t: t.extractall(dest, filter="data")
    else:
        from . import tools
        exe = tools.seven_zip(reporter)
        subprocess.run([exe, "x", "-y", f"-o{dest}", str(archive)], check=True, capture_output=True, timeout=1800)
    return dest

BUNDLES = (".vst3", ".clap", ".component", ".aaxplugin", ".vst", ".app", ".bundle", ".lv2")

def _holds_plugins(d: Path) -> bool:
    return any(x.suffix.lower() in PLUGIN_SUFFIXES + BUNDLES + INSTALLERS for x in d.rglob("*"))

def _vst2_extras(f: Found) -> list[Path]:
    """Files and data folders beside a VST2 dll that the plugin may load: not
    reading matter, not other plugins or installers, not other systems' builds."""
    out = []
    for x in sorted(f.src.parent.iterdir()):
        if x == f.src or _junk(Path(x.name)): continue
        s = x.suffix.lower()
        if x.is_dir():
            if s not in BUNDLES and not _holds_plugins(x): out.append(x)
        elif s == ".dll":
            if not _vst2(x): out.append(x)        # a library the plugin loads, not another plugin
        elif s not in DOCS + PLUGIN_SUFFIXES + INSTALLERS:
            out.append(x)
    return out

def install(p, src: Path, reporter, name: str | None = None) -> Result:
    """Copy the plugins in `src` (archive, plugin file or folder) into the prefix."""
    src = Path(src); r = reporter; res = Result()
    label = name or src.name
    work = None
    if is_archive(src):
        r.step(f"Unpacking {label}")
        work = paths.DOWNLOADS / "unpacked" / src.name
        try: unpack(src, work, r)
        except Exception as e: r.fail(str(e)); raise
        r.ok(); root = work
    else:
        root = src
    found, res.skipped, installers = scan(root)
    if not found:
        res.installers = installers
        if not installers and work is not None: shutil.rmtree(work, ignore_errors=True)
        return res
    r.step(f"Copying plugins from {label}")
    done: set[Path] = set()
    for f in found:
        base = p.drive_c / DEST[f.fmt]; base.mkdir(parents=True, exist_ok=True)
        extras = _vst2_extras(f) if f.fmt == "VST2" else []
        if extras:              # the plugin keeps its files beside it, in a folder of its own
            folder = base / _folder_name(src if is_archive(src) else f.src)
            if folder not in done: _replace(folder); folder.mkdir(parents=True)
            target = folder / f.src.name
            for x in extras:
                if not (folder / x.name).exists(): _copy(x, folder / x.name)
            done.add(folder)
        else:
            target = base / f.src.name; _replace(target)
        _copy(f.src, target)
        res.installed.append((f.fmt, target)); r.log(f"{f.fmt}: {target}")
    r.ok(", ".join(f"{fmt} {t.name}" for fmt, t in res.installed))
    for n, why in res.skipped: r.log(f"left out {n}: {why}")
    if work is not None: shutil.rmtree(work, ignore_errors=True)
    return res

def _replace(target: Path):
    if target.is_dir() and not target.is_symlink(): shutil.rmtree(target)
    elif target.exists() or target.is_symlink(): target.unlink()

def _copy(a: Path, b: Path):
    if a.is_dir(): shutil.copytree(a, b, ignore=shutil.ignore_patterns(*JUNK, "._*"))
    else: shutil.copy2(a, b)

def _folder_name(src: Path) -> str:
    n = src.name
    s = _suffix(src)
    return n[: -len(s)] if s and n.lower().endswith(s) else n
