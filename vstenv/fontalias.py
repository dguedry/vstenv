"""A "Segoe UI" font family for DirectWrite, made from Microsoft's Selawik.

Windows programs ask DirectWrite for "Segoe UI" when they draw dialog text.
GDI honours the registry's FontSubstitutes (vstenv maps Segoe UI to DejaVu
Sans there), but DirectWrite looks the family up by name in the system font
collection, and when it is absent the text is simply not drawn: Steinberg's
dialogs (HALion's "Are you sure you want to quit?", the Activation Manager's
notices, the crash reporter) came up with an icon, blank buttons and no
message. Selawik is Microsoft's open-source, metric-compatible stand-in for
Segoe UI (SIL Open Font License 1.1). This module fetches the Selawik
release, rewrites each face's name table so the family is called "Segoe UI"
(the same aliasing GDI does through FontSubstitutes, done inside the file so
DirectWrite sees it too), installs the faces into the prefix's Fonts folder
and registers them. No font library is needed: the name table is rewritten
by hand, and every other table is copied unchanged.
"""
import struct
from pathlib import Path
from . import paths
from .download import fetch
from .progress import null_reporter
from .wine import Prefix

SELAWIK_URL = "https://github.com/microsoft/Selawik/releases/download/1.01/Selawik_Release.zip"
SELAWIK_SHA256 = "3f62c51e05e3b5a1e6241cf92a371f0be2ea1183aa87b30718bbd40832a8d423"
FAMILY = "Segoe UI"
# Selawik file -> (installed file, GDI family name, style, PostScript name, registry entry)
FACES = {
    "selawk.ttf":   ("segoeui.ttf",   "Segoe UI",           "Regular",  "SegoeUI",           "Segoe UI (TrueType)"),
    "selawkb.ttf":  ("segoeuib.ttf",  "Segoe UI",           "Bold",     "SegoeUI-Bold",      "Segoe UI Bold (TrueType)"),
    "selawksb.ttf": ("segoeuisb.ttf", "Segoe UI Semibold",  "Regular",  "SegoeUI-Semibold",  "Segoe UI Semibold (TrueType)"),
    "selawkl.ttf":  ("segoeuil.ttf",  "Segoe UI Light",     "Regular",  "SegoeUI-Light",     "Segoe UI Light (TrueType)"),
    "selawksl.ttf": ("segoeuisl.ttf", "Segoe UI Semilight", "Regular",  "SegoeUI-Semilight", "Segoe UI Semilight (TrueType)"),
}
MARK = "vstenv alias of Selawik"

# --- TrueType name table ------------------------------------------------------------
def _checksum(data: bytes) -> int:
    data += b"\0" * (-len(data) % 4)
    return sum(struct.unpack(f">{len(data) // 4}I", data)) & 0xFFFFFFFF

def read_names(ttf: bytes) -> dict[int, str]:
    """nameID -> string (Windows Unicode records preferred)."""
    num_tables = struct.unpack_from(">H", ttf, 4)[0]
    for i in range(num_tables):
        tag, _, off, length = struct.unpack_from(">4sIII", ttf, 12 + 16 * i)
        if tag == b"name": break
    else: raise ValueError("no name table")
    fmt, count, str_off = struct.unpack_from(">HHH", ttf, off)
    out = {}
    for i in range(count):
        pid, eid, lang, nid, slen, soff = struct.unpack_from(">HHHHHH", ttf, off + 6 + 12 * i)
        raw = ttf[off + str_off + soff: off + str_off + soff + slen]
        if pid == 3 and eid in (0, 1): out[nid] = raw.decode("utf-16-be", "replace")
        elif nid not in out and pid == 1: out[nid] = raw.decode("mac-roman", "replace")
    return out

def rename(ttf: bytes, family: str, style: str, postscript: str, typographic_family: str | None = None,
           typographic_style: str | None = None, note: str = MARK) -> bytes:
    """A copy of the font with its naming records replaced: family (1), style (2),
    unique (3), full (4), PostScript (6), typographic family/style (16/17) and a
    description (10) noting the origin. Records for other name IDs are kept."""
    num_tables = struct.unpack_from(">H", ttf, 4)[0]
    tables = []
    for i in range(num_tables):
        tag, csum, off, length = struct.unpack_from(">4sIII", ttf, 12 + 16 * i)
        tables.append([tag, ttf[off:off + length]])
    idx = next((i for i, (t, _) in enumerate(tables) if t == b"name"), None)
    if idx is None: raise ValueError("no name table")
    old = tables[idx][1]
    fmt, count, str_off = struct.unpack_from(">HHH", old)
    typographic_family = typographic_family or family
    typographic_style = typographic_style or style
    full = family if style == "Regular" else f"{family} {style}"
    new_values = {1: family, 2: style, 3: f"{note}: {postscript}", 4: full, 6: postscript, 10: note,
                  16: typographic_family, 17: typographic_style}
    records, strings = [], bytearray()
    seen = set()
    for i in range(count):
        pid, eid, lang, nid, slen, soff = struct.unpack_from(">HHHHHH", old, 6 + 12 * i)
        if nid in new_values:
            text = new_values[nid]
            data = text.encode("utf-16-be") if pid in (0, 3) else text.encode("mac-roman", "replace")
        else:
            data = old[str_off + soff: str_off + soff + slen]
        seen.add((pid, eid, lang, nid))
        records.append((pid, eid, lang, nid, len(data), len(strings))); strings += data
    for nid, text in new_values.items():       # a face may lack 16/17 or 10: add Windows records
        if not any(k[3] == nid and k[0] == 3 for k in seen):
            data = text.encode("utf-16-be")
            records.append((3, 1, 0x409, nid, len(data), len(strings))); strings += data
    records.sort(key=lambda r: (r[0], r[1], r[2], r[3]))
    head = struct.pack(">HHH", fmt, len(records), 6 + 12 * len(records))
    body = b"".join(struct.pack(">HHHHHH", *r) for r in records)
    tables[idx][1] = head + body + bytes(strings)
    # rebuild the file: directory, then tables 4-byte aligned; head.checkSumAdjustment recomputed
    out = bytearray(ttf[:12])
    offset = 12 + 16 * len(tables)
    entries = []
    blobs = bytearray()
    for tag, data in tables:
        if tag == b"head":
            data = bytearray(data); struct.pack_into(">I", data, 8, 0); data = bytes(data)
        entries.append((tag, _checksum(data), offset + len(blobs), len(data)))
        blobs += data + b"\0" * (-len(data) % 4)
    for tag, csum, off, length in entries: out += struct.pack(">4sIII", tag, csum, off, length)
    out += blobs
    total = (0xB1B0AFBA - _checksum(bytes(out))) & 0xFFFFFFFF
    head_off = next(off for tag, _, off, _ in entries if tag == b"head")
    struct.pack_into(">I", out, head_off + 8, total)
    return bytes(out)

# --- install into the prefix ---------------------------------------------------------
def fonts_dir(p: Prefix) -> Path: return p.drive_c / "windows/Fonts"

def installed(p: Prefix) -> list[str]:
    """The alias faces present in the prefix (by their installed file names)."""
    return [dst for dst, *_ in FACES.values() if (fonts_dir(p) / dst).exists()]

def status(p: Prefix) -> dict:
    have = installed(p)
    return {"installed": len(have) == len(FACES), "faces": have, "missing": [dst for dst, *_ in FACES.values() if dst not in have]}

def install(p: Prefix, reporter=None, force=False) -> list[str]:
    """Fetch Selawik, write the Segoe UI alias faces into the prefix and register
    them; returns the installed file names. Idempotent."""
    import zipfile
    r = null_reporter(reporter)
    r.step("Segoe UI font family for DirectWrite (dialog text)")
    fdir = fonts_dir(p); fdir.mkdir(parents=True, exist_ok=True)
    if not force and not status(p)["missing"]:
        r.skip(f"{len(FACES)} faces in place"); return installed(p)
    zpath = fetch(SELAWIK_URL, paths.DOWNLOADS / "Selawik_Release.zip", sha256=SELAWIK_SHA256, reporter=r, label="Selawik fonts")
    written = []
    with zipfile.ZipFile(zpath) as z:
        for src, (dst, family, style, psname, _) in FACES.items():
            data = z.read(src)
            typo_style = {"selawksb.ttf": "Semibold", "selawkl.ttf": "Light", "selawksl.ttf": "Semilight"}.get(src, style)
            out = rename(data, family, style, psname, typographic_family=FAMILY, typographic_style=typo_style)
            (fdir / dst).write_bytes(out); written.append(dst)
    reg = ["Windows Registry Editor Version 5.00", "", r"[HKEY_LOCAL_MACHINE\Software\Microsoft\Windows NT\CurrentVersion\Fonts]"]
    reg += [f'"{entry}"="{dst}"' for dst, _, _, _, entry in FACES.values()] + [""]
    rc = p.reg_import("\r\n".join(reg), "vstenv-segoe.reg")
    (r.ok if rc == 0 else r.fail)(f"{len(written)} faces from Selawik" + ("" if rc == 0 else f"; registering them failed (regedit exit {rc})"))
    return installed(p)
