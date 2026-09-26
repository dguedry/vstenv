"""Write a minimal 64-bit Windows DLL whose named exports all return E_NOTIMPL (0x80004001).

No compiler needed: one .text section holding six bytes of code and the export
table. Useful to find out how a program behaves when a Wine-side API is made to
fail cleanly (drop the DLL into the prefix's system32 and set a per-application
DllOverrides entry to "native"). First use, 2026-09-26: a dcomp.dll stub showed
that Steinberg's graphics2d has no path without DirectComposition (the abort
merely became their "serious graphic driver related issue" dialog).

    python3 scripts/make_stub_dll.py out.dll ExportA ExportB ...
"""
import struct, sys
from pathlib import Path

def build(dll_name: str, exports: list[str]) -> bytes:
    exports = sorted(exports)                       # the loader binary-searches names
    code = bytes.fromhex("b801400080c3")           # mov eax, 0x80004001 ; ret
    text_rva, file_align, sect_align = 0x1000, 0x200, 0x1000
    # export directory layout inside .text, after the code (16-byte aligned)
    exp_rva = text_rva + 0x10
    n = len(exports)
    funcs_rva = exp_rva + 40
    names_rva = funcs_rva + 4 * n
    ords_rva = names_rva + 4 * n
    strings_rva = ords_rva + 2 * n
    blob = bytearray(); name_rvas = []
    for e in exports:
        name_rvas.append(strings_rva + len(blob)); blob += e.encode() + b"\0"
    dllname_rva = strings_rva + len(blob); blob += dll_name.encode() + b"\0"
    expdir = struct.pack("<IIHHIIIIIII", 0, 0, 0, 0, dllname_rva, 1, n, n, funcs_rva, names_rva, ords_rva)
    table = b"".join(struct.pack("<I", text_rva) for _ in exports) + b"".join(struct.pack("<I", r) for r in name_rvas) \
          + b"".join(struct.pack("<H", i) for i in range(n))
    section = code.ljust(0x10, b"\0") + expdir + table + bytes(blob)
    exp_size = len(expdir) + len(table) + len(blob)
    raw_size = (len(section) + file_align - 1) // file_align * file_align
    section = section.ljust(raw_size, b"\0")
    # headers
    dos = bytearray(64); dos[:2] = b"MZ"; struct.pack_into("<I", dos, 0x3C, 0x40)
    coff = struct.pack("<HHIIIHH", 0x8664, 1, 0, 0, 0, 240, 0x2022)   # x64, 1 section, DLL | EXECUTABLE | LARGE_ADDRESS_AWARE
    dirs = [(0, 0)] * 16; dirs[0] = (exp_rva, exp_size)
    opt = struct.pack("<HBBIIIIIQIIHHHHHHIIIIHHQQQQII", 0x20B, 14, 0, raw_size, 0, 0, 0, text_rva, 0x180000000, sect_align, file_align,
                      6, 0, 0, 0, 6, 0, 0, sect_align + raw_size + sect_align - 1 & ~(sect_align - 1) if False else text_rva + ((raw_size + sect_align - 1) // sect_align) * sect_align,
                      0x200, 0, 2, 0x160, 0x100000, 0x1000, 0x100000, 0x1000, 0, 16)
    opt += b"".join(struct.pack("<II", r, s) for r, s in dirs)
    sect = struct.pack("<8sIIIIIIHHI", b".text", len(section), text_rva, raw_size, 0x200, 0, 0, 0, 0, 0x60000020)
    headers = (bytes(dos) + b"PE\0\0" + coff + opt + sect).ljust(0x200, b"\0")
    assert len(coff) == 20 and len(opt) == 240, (len(coff), len(opt))
    return headers + section

if __name__ == "__main__":
    out = Path(sys.argv[1]); out.write_bytes(build("dcomp.dll", sys.argv[2:])); print("wrote", out, out.stat().st_size, "bytes")
