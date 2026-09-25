"""Read what we need from Windows executables without running them: the file
version, and the application icon (for desktop menu entries)."""
import struct
from pathlib import Path

RT_ICON, RT_GROUP_ICON = 3, 14

def version(exe: Path) -> str | None:
    """FileVersion from the PE VS_FIXEDFILEINFO block (signature 0xFEEF04BD)."""
    try: d = Path(exe).read_bytes()
    except OSError: return None
    key = "VS_VERSION_INFO".encode("utf-16-le")
    i, k = -1, d.find(key)
    while k >= 0:  # signature follows the key within a few padding bytes
        j = d.find(b"\xbd\x04\xef\xfe", k, k + 80)
        if j >= 0: i = j; break
        k = d.find(key, k + 1)
    if i < 0 or i + 24 > len(d): return None
    ms, ls = struct.unpack_from("<II", d, i + 8)
    return f"{ms >> 16}.{ms & 0xffff}.{ls >> 16}" + (f".{ls & 0xffff}" if ls & 0xffff else "")

def _resources(d: bytes):
    """(file offset of the resource directory, sections) or None."""
    if len(d) < 0x40 or d[:2] != b"MZ": return None
    pe = struct.unpack_from("<I", d, 0x3c)[0]
    if d[pe:pe + 4] != b"PE\0\0": return None
    nsec = struct.unpack_from("<H", d, pe + 6)[0]; opt_size = struct.unpack_from("<H", d, pe + 20)[0]
    opt = pe + 24; magic = struct.unpack_from("<H", d, opt)[0]
    dirs = opt + (112 if magic == 0x20B else 96)          # PE32+ / PE32 data directories
    res_rva = struct.unpack_from("<I", d, dirs + 2 * 8)[0]  # entry 2: resources
    secs = []
    for i in range(nsec):
        s = opt + opt_size + 40 * i
        vsize, vaddr, rsize, raw = struct.unpack_from("<IIII", d, s + 8)
        secs.append((vaddr, max(vsize, rsize), raw))
    off = _offset(secs, res_rva)
    return (off, secs) if res_rva and off is not None else None

def _offset(secs, rva):
    for vaddr, size, raw in secs:
        if vaddr <= rva < vaddr + size: return raw + (rva - vaddr)
    return None

def _entries(d, base, off):
    """[(id or None for a named entry, offset, is_directory)] of one resource directory."""
    n_named, n_id = struct.unpack_from("<HH", d, off + 12)
    out = []
    for i in range(n_named + n_id):
        name, data = struct.unpack_from("<II", d, off + 16 + 8 * i)
        out.append((None if name & 0x80000000 else name, base + (data & 0x7fffffff), bool(data & 0x80000000)))
    return out

def _leaves(d, base, secs, type_off):
    """(id, bytes) for every resource of one type (first language of each)."""
    for rid, noff, isdir in _entries(d, base, type_off):
        if not isdir: continue
        langs = _entries(d, base, noff)
        if not langs: continue
        rva, size = struct.unpack_from("<II", d, langs[0][1])
        o = _offset(secs, rva)
        if o is not None: yield rid, d[o:o + size]

def icon(exe: Path) -> bytes | None:
    """The program's first icon group as a .ico file (all its sizes), or None."""
    try: d = Path(exe).read_bytes()
    except OSError: return None
    try:
        res = _resources(d)
        if res is None: return None
        base, secs = res
        types = {tid: off for tid, off, isdir in _entries(d, base, base) if isdir}
        if RT_GROUP_ICON not in types or RT_ICON not in types: return None
        groups = list(_leaves(d, base, secs, types[RT_GROUP_ICON]))
        if not groups: return None
        icons = dict(_leaves(d, base, secs, types[RT_ICON]))
        grp = groups[0][1]
        count = struct.unpack_from("<H", grp, 4)[0]
        entries, images = [], []
        for i in range(count):
            w, h, colors, reserved, planes, bits, size, rid = struct.unpack_from("<BBBBHHIH", grp, 6 + 14 * i)
            img = icons.get(rid)
            if img is None: continue
            entries.append((w, h, colors, reserved, planes, bits, len(img))); images.append(img)
        if not images: return None
        out = bytearray(struct.pack("<HHH", 0, 1, len(images)))
        off = 6 + 16 * len(images)
        for (w, h, colors, reserved, planes, bits, size), img in zip(entries, images):
            out += struct.pack("<BBBBHHII", w, h, colors, reserved, planes, bits, size, off); off += size
        for img in images: out += img
        return bytes(out)
    except (struct.error, IndexError, ValueError):
        return None
