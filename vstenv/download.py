"""Streaming downloads with hash verification and progress."""
import base64, hashlib, urllib.request
from pathlib import Path
from .progress import null_reporter

UA = "vstenv/0.1 (+https://github.com/dguedry/vstenv)"

def fetch(url: str, dest: Path, *, sha256: str | None = None, sha512_b64: str | None = None,
          reporter=None, label: str | None = None, force=False) -> Path:
    """Download url to dest (atomic via .part). Skips if dest exists and its
    hash matches (or no hash given). Raises on hash mismatch."""
    r = null_reporter(reporter); dest = Path(dest); label = label or dest.name
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and not force:
        if (sha256 is None and sha512_b64 is None) or _hash_ok(dest, sha256, sha512_b64):
            r.log(f"cached: {dest.name}"); return dest
    part = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as resp, open(part, "wb") as f:
        total = int(resp.headers.get("Content-Length") or 0) or None
        done = 0
        while True:
            chunk = resp.read(1 << 20)
            if not chunk: break
            f.write(chunk); done += len(chunk); r.progress(done, total, label)
    if not _hash_ok(part, sha256, sha512_b64):
        part.unlink(missing_ok=True); raise RuntimeError(f"hash mismatch for {url}")
    part.replace(dest); return dest

def text(url: str, timeout=30) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")

def _hash_ok(path: Path, sha256, sha512_b64) -> bool:
    if sha256 is None and sha512_b64 is None: return True
    h256, h512 = hashlib.sha256(), hashlib.sha512()
    with open(path, "rb") as f:
        for blk in iter(lambda: f.read(1 << 20), b""):
            h256.update(blk); h512.update(blk)
    if sha256 is not None and h256.hexdigest().lower() != sha256.lower(): return False
    if sha512_b64 is not None and base64.b64encode(h512.digest()).decode() != sha512_b64: return False
    return True
