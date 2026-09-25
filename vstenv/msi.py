"""Read tables from an MSI database (no msitools needed; requires olefile)."""
import struct

def _mschar(x):
    if x < 10: return chr(x + ord('0'))
    if x < 36: return chr(x - 10 + ord('A'))
    if x < 62: return chr(x - 36 + ord('a'))
    return '.' if x == 62 else '_'

def _decode_name(name):
    out = []
    for ch in name:
        c = ord(ch)
        if 0x3800 <= c < 0x4800:
            c -= 0x3800; out.append(_mschar(c & 0x3f) + _mschar((c >> 6) & 0x3f))
        elif 0x4800 <= c < 0x4840: out.append(_mschar(c - 0x4800))
        elif c == 0x4840: out.append('!')
        else: out.append(ch)
    return ''.join(out)

class Msi:
    def __init__(self, path):
        import olefile
        self.ole = olefile.OleFileIO(str(path))
        self.streams = {_decode_name(e[0]): e[0] for e in self.ole.listdir()}
        pool, data = self._read('!_StringPool'), self._read('!_StringData')
        self.strings = ['']; off = 0; i = 4
        self.codepage = struct.unpack_from('<H', pool, 0)[0]
        while i + 4 <= len(pool):
            size, refs = struct.unpack_from('<HH', pool, i); i += 4
            if size == 0 and refs != 0:  # long string: real size in next dword
                size = struct.unpack_from('<I', pool, i)[0]; i += 4
            self.strings.append(data[off:off + size].decode('utf-8', 'replace')); off += size
        self.wide = len(self.strings) > 0x10000 or (struct.unpack_from('<H', pool, 2)[0] & 0x8000)
        self.columns = self._columns()

    def _read(self, n): return self.ole.openstream(self.streams[n]).read()

    def _columns(self):
        data = self._read('!_Columns'); n = len(data) // 8
        tbl = [self._sval(data, i) for i in range(n)]
        num = [struct.unpack_from('<H', data, 2 * n + 2 * i)[0] for i in range(n)]
        name = [self._sval(data, 2 * n + i) for i in range(n)]
        typ = [struct.unpack_from('<H', data, 6 * n + 2 * i)[0] for i in range(n)]
        cols = {}
        for t, nu, na, ty in zip(tbl, num, name, typ):
            cols.setdefault(t, []).append((nu & 0x7fff, na, ty))
        for t in cols: cols[t].sort()
        return cols

    def _sval(self, data, idx):
        w = 2
        return self.strings[struct.unpack_from('<H', data, w * idx)[0]]

    def rows(self, table: str):
        cols = self.columns.get(table)
        if not cols: raise KeyError(f"no table {table}")
        data = self._read('!' + table)
        sizes = []
        for _, _, ty in cols:
            if ty & 0x0800: sizes.append(2)               # string ref
            elif (ty & 0xff) == 4: sizes.append(4)        # int32
            else: sizes.append(2)                         # int16
        rowsize = sum(sizes)
        nrows = len(data) // rowsize if rowsize else 0
        names = [c[1] for c in cols]
        out = []; off = 0
        colblocks = []
        for sz in sizes:
            colblocks.append(off); off += sz * nrows
        for ri in range(nrows):
            row = {}
            for (nu, na, ty), sz, base in zip(cols, sizes, colblocks):
                o = base + sz * ri
                v = struct.unpack_from('<H' if sz == 2 else '<I', data, o)[0]
                if ty & 0x0800:
                    row[na] = self.strings[v] if v < len(self.strings) else f'#{v}'
                elif sz == 2:
                    row[na] = None if v == 0 else v - 0x8000
                else:
                    row[na] = None if v == 0 else v - 0x80000000
            out.append(row)
        return out
