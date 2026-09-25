"""Patch files inside an Electron app.asar in place.

Layout: 4 x uint32 pickle header (offset 12 holds the JSON length), the JSON
directory at 16, padded to 4 bytes, then every file's bytes at header-relative
offsets. Each unpacked file may carry an `integrity` block (SHA-256 of the
whole file and of 4 MiB blocks) which newer Electron validates when the
EnableEmbeddedAsarIntegrityValidation fuse is on; it is recomputed for every
file we change. Apps that embed the header hash in their exe (Native Access)
are handled by native_access, which also rewrites that resource.
"""
import hashlib, json, re, shutil, struct
from pathlib import Path

BLOCK = 4 * 1024 * 1024

def _header(data: bytes):
    json_len = struct.unpack_from("<I", data, 12)[0]
    hjson = data[16:16 + json_len]
    return json.loads(hjson.rstrip(b"\0")), 16 + ((json_len + 3) & ~3), hjson

def _entries(header: dict):
    out = []
    def walk(node, path):
        for k, v in node.get("files", {}).items():
            q = f"{path}/{k}" if path else k
            if "files" in v: walk(v, q)
            elif not v.get("unpacked"): out.append((q, v))
    walk(header, ""); return out

def read(asar: Path, path: str) -> bytes:
    data = asar.read_bytes(); header, base, _ = _header(data)
    node = header
    for part in path.strip("/").split("/"): node = node["files"][part]
    o = int(node["offset"]); return data[base + o: base + o + node["size"]]

def _integrity(b: bytes) -> dict:
    return {"algorithm": "SHA256", "hash": hashlib.sha256(b).hexdigest(), "blockSize": BLOCK,
            "blocks": [hashlib.sha256(b[i:i + BLOCK]).hexdigest() for i in range(0, len(b), BLOCK)]}

def patch(asar: Path, edits: dict, keep_orig=True) -> dict:
    """edits: {path_regex: fn(bytes) -> bytes|None}. fn returns None to leave the
    file alone. Returns {regex: 'patched'|'unchanged'|'missing'} and the new
    header JSON hash (key '_header_sha256'), for apps that pin it elsewhere."""
    data = asar.read_bytes(); header, base, hjson = _header(data)
    entries = _entries(header)
    result = {rx: "missing" for rx in edits}; replaced: dict[str, bytes] = {}
    for q, node in entries:
        for rx, fn in edits.items():
            if re.fullmatch(rx, q):
                o = int(node["offset"]); blob = data[base + o: base + o + node["size"]]
                nb = fn(blob)
                result[rx] = "patched" if nb is not None and nb != blob else "unchanged"
                if nb is not None and nb != blob: replaced[q] = nb
    result["_old_header_sha256"] = hashlib.sha256(hjson).hexdigest()
    if not replaced: result["_header_sha256"] = result["_old_header_sha256"]; return result
    entries.sort(key=lambda e: int(e[1]["offset"]))
    chunks, cur = [], 0
    for q, node in entries:
        if q in replaced: blob = replaced[q]; node["integrity"] = _integrity(blob) if "integrity" in node else node.get("integrity")
        else: o = int(node["offset"]); blob = data[base + o: base + o + node["size"]]
        if node.get("integrity") is None: node.pop("integrity", None)
        node["offset"] = str(cur); node["size"] = len(blob); chunks.append(blob); cur += len(blob)
    hj = json.dumps(header, separators=(",", ":")).encode("utf-8")
    pad = ((len(hj) + 3) & ~3) - len(hj)
    head = struct.pack("<IIII", 4, len(hj) + pad + 8, len(hj) + pad + 4, len(hj))
    if keep_orig:
        orig = asar.with_suffix(".asar.orig")
        if not orig.exists(): shutil.copy2(asar, orig)
    tmp = asar.with_suffix(".asar.tmp")
    with open(tmp, "wb") as f:
        f.write(head); f.write(hj); f.write(b"\0" * pad)
        for c in chunks: f.write(c)
    tmp.replace(asar)
    result["_header_sha256"] = hashlib.sha256(hj).hexdigest()
    return result
