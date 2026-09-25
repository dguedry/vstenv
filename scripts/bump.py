#!/usr/bin/env python3
"""Bump the version across the package, pyproject, and metainfo.

Single source of truth for both scripts/release.sh (local) and the auto-tag CI
job (on push to main). Given a version and a one-line changelog, it edits:
  - vstenv/__init__.py  __version__
  - pyproject.toml       version
  - data/…metainfo.xml   new <release> entry (dated today, UTC)

Usage:  scripts/bump.py <version> "<changelog>"
        scripts/bump.py --next-patch          # print the next patch version and exit
"""
import sys, re, datetime
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent.parent
INIT = ROOT / "vstenv/__init__.py"
PYPROJECT = ROOT / "pyproject.toml"
METAINFO = ROOT / "data/io.github.dguedry.vstenv.metainfo.xml"

def current() -> str:
    m = re.search(r'__version__ = "([^"]*)"', INIT.read_text())
    if not m: sys.exit("bump: could not read __version__")
    return m.group(1)

def next_patch(v: str) -> str:
    a, b, c = (int(x) for x in v.split("."))
    return f"{a}.{b}.{c + 1}"

def sub_one(path: Path, pattern: str, repl: str):
    s = path.read_text()
    s2, n = re.subn(pattern, repl, s, count=1)
    if n != 1: sys.exit(f"bump: could not update {path} (pattern not found once)")
    path.write_text(s2)

def bump(ver: str, note: str):
    if not re.fullmatch(r"\d+\.\d+\.\d+", ver): sys.exit(f"bump: bad version {ver!r}")
    if f'version="{ver}"' in METAINFO.read_text(): sys.exit(f"bump: metainfo already has {ver}")
    sub_one(INIT, r'__version__ = "[^"]*"', f'__version__ = "{ver}"')
    sub_one(PYPROJECT, r'(?m)^version = "[^"]*"', f'version = "{ver}"')
    today = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    entry = (f'    <release version="{ver}" date="{today}">\n'
             f'      <description>\n'
             f'        <p>{escape(note)}</p>\n'
             f'      </description>\n'
             f'    </release>\n')
    sub_one(METAINFO, r'(<releases>\n)', lambda m: m.group(1) + entry)

if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "--next-patch":
        print(next_patch(current())); sys.exit(0)
    if len(sys.argv) != 3:
        sys.exit('usage: bump.py <version> "<changelog>"  |  bump.py --next-patch')
    bump(sys.argv[1], sys.argv[2])
    print(f"bumped to {sys.argv[1]}")
