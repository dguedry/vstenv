"""Native Access under Wine: install it from the installer the user downloads,
and every fix it needs. Each public function is idempotent and reports through
a Reporter. See the README for the diagnosis behind each fix.
"""
import hashlib, json, os, re, shutil, struct, subprocess, tempfile, time
from pathlib import Path
from ... import paths, pe
from ...download import text
from ...progress import null_reporter
from ...runtime import need as _need
from ...wine import Prefix

NA_DOWNLOAD_PAGE = "https://www.native-instruments.com/pages/native-access"
UPDATE_FEED = "https://na-update.native-instruments.com"   # version lookup only; the installer is never downloaded by this app
STACK_RESERVE = 0x4000000  # 64MB
# NA versions validated on this stack (all fixes apply, window renders, installs work)
KNOWN_GOOD = {"3.23.0", "3.25.2", "3.26.0"}
# Versions that do not work on this stack, with the reason shown to the user.
# (Empty for now: 3.26.0 was listed here for a day after a transient prefix
# state made its child processes and renderer fail; a clean setup cleared it.)
KNOWN_BAD: dict[str, str] = {}
DEP_MARK = "/*NA_DEP_PATCH*/"
NTK_PORTS = (7865, 5563, 5146)   # NTKDaemon listens on 127.0.0.1 — one daemon per machine

NA_REL = Path("Program Files/Native Instruments/Native Access")
NTK_REL = Path("Program Files/Common Files/Native Instruments/NTKDaemon")

def na_dir(p: Prefix) -> Path: return p.drive_c / NA_REL
def na_exe(p: Prefix) -> Path: return na_dir(p) / "Native Access.exe"
def na_roaming(p: Prefix) -> Path: return p.user_dir / "AppData/Roaming/Native Instruments/Native Access"
def na_log(p: Prefix) -> Path: return p.public_docs / "Native Instruments/Logs/Native Access/native-access.log"

# --- feed / download ------------------------------------------------------------
def feed() -> dict:
    """Parse NA's electron-updater feed (latest.yml) without pyyaml."""
    y = text(f"{UPDATE_FEED}/latest.yml")
    ver = re.search(r"^version:\s*(\S+)", y, re.M)
    path = re.search(r"^path:\s*(\S+)", y, re.M)
    sha = re.search(r"^sha512:\s*(\S+)", y, re.M)
    size = re.search(r"size:\s*(\d+)", y)
    return {"version": ver.group(1), "file": path.group(1), "sha512": sha.group(1), "size": int(size.group(1)) if size else None}

def latest_version() -> str | None:
    """Current NA version from NI's feed, for display only."""
    try: return feed()["version"]
    except Exception: return None

# --- install -----------------------------------------------------------------------
def extract_app(installer: Path, dest_dir: Path):
    """The NSIS package does not run under Wine; pull app-64.7z out of it."""
    _need("7z")
    with tempfile.TemporaryDirectory() as t:
        subprocess.run(["7z", "e", "-y", f"-o{t}", str(installer), "$PLUGINSDIR/app-64.7z"], check=True, capture_output=True)
        app7z = Path(t) / "app-64.7z"
        if not app7z.exists(): raise RuntimeError("installer has no $PLUGINSDIR/app-64.7z")
        subprocess.run(["7z", "x", "-y", f"-o{dest_dir}", str(app7z)], check=True, capture_output=True)
    if not (dest_dir / "Native Access.exe").exists(): raise RuntimeError("extracted app has no Native Access.exe")

def install(p: Prefix, installer: Path, reporter=None, keep_previous=True):
    r = null_reporter(reporter)
    r.step("Installing Native Access application files")
    d = na_dir(p)
    if d.exists():
        prev = d.with_name("Native Access.prev")
        shutil.rmtree(prev, ignore_errors=True)
        d.rename(prev) if keep_previous else shutil.rmtree(d)
    tmp = d.with_name("Native Access.new"); shutil.rmtree(tmp, ignore_errors=True)
    extract_app(installer, tmp); tmp.rename(d)
    r.ok(pe.version(na_exe(p)) or "installed")

# --- fix 1: PE stack reserve ---------------------------------------------------------
def stack_patch(exe: Path) -> str:
    """Returns 'patched' | 'already' ."""
    with open(exe, "r+b") as f:
        hdr = f.read(0x400)
        opt = struct.unpack_from("<I", hdr, 0x3c)[0] + 24
        if struct.unpack_from("<H", hdr, opt)[0] != 0x20B: raise RuntimeError("not a PE32+ exe")
        off = opt + 72
        reserve = struct.unpack_from("<Q", hdr, off)[0]
        if reserve >= STACK_RESERVE: return "already"
        bak = exe.with_suffix(".exe.bak")
        if not bak.exists(): shutil.copy2(exe, bak)
        f.seek(off); f.write(struct.pack("<Q", STACK_RESERVE))
        return "patched"

def stack_ok(exe: Path) -> bool:
    try:
        hdr = exe.read_bytes()[:0x400]
        opt = struct.unpack_from("<I", hdr, 0x3c)[0] + 24
        return struct.unpack_from("<Q", hdr, opt + 72)[0] >= STACK_RESERVE
    except Exception: return False

# --- fix 4: NTK Daemon helper service ----------------------------------------------------------------
def ntk_daemon(p: Prefix, reporter=None) -> str | None:
    r = null_reporter(reporter); _need("7z")
    r.step("Installing NTK Daemon helper service")
    setups = sorted((na_dir(p) / "resources/daemon").rglob("NTKDaemon*Setup*.exe"))
    if not setups: r.fail("NTKDaemon setup exe not found in NA resources"); return None
    setup = setups[-1]
    dest = p.drive_c / NTK_REL; dest.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as t:
        subprocess.run(["7z", "x", "-y", f"-o{t}", str(setup)], check=True, capture_output=True)
        exes = list(Path(t).rglob("NTKDaemon.exe"))
        if not exes: r.fail("NTKDaemon.exe not in extracted setup"); return None
        if p.is_running("NTKDaemon.exe"): p.sc("stop", "NTKDaemon"); time.sleep(2)
        shutil.copytree(exes[0].parent, dest, dirs_exist_ok=True)
    ver = pe.version(dest / "NTKDaemon.exe")
    binpath = "C:\\Program Files\\Common Files\\Native Instruments\\NTKDaemon\\NTKDaemon.exe"
    # sc wants "binPath=" and the value as separate argv tokens
    if not p.reg_query(r"HKLM\System\CurrentControlSet\Services\NTKDaemon").get("ImagePath"):
        p.sc("create", "NTKDaemon", "binPath=", f'"{binpath}"', "start=", "auto")
    # belt and braces: SERVICE_AUTO_START so it comes up with every prefix boot
    p.reg_add(r"HKLM\System\CurrentControlSet\Services\NTKDaemon", "Start", "2", "REG_DWORD")
    p.sc("start", "NTKDaemon"); time.sleep(2)
    running = p.is_running("NTKDaemon.exe") or "RUNNING" in p.sc("query", "NTKDaemon")
    r.ok(f"{ver or ''} {'running' if running else 'installed (starts with prefix)'}".strip())
    return ver

# --- fix 5: NA settings -----------------------------------------------------------------------------------
def config(p: Prefix, reporter=None):
    r = null_reporter(reporter)
    r.step("Enabling hardware acceleration in NA settings")
    d = na_roaming(p); n = 0
    if d.exists():
        for f in d.glob("*.json"):
            try: j = json.loads(f.read_text())
            except Exception: continue
            if isinstance(j, dict) and j.get("disableHardwareAcceleration") is True:
                j["disableHardwareAcceleration"] = False
                f.write_text(json.dumps(j, indent=2)); n += 1
    r.ok(f"{n} file(s) updated" if n else "nothing to change (first run)")

# --- fix 8: a writable download location ------------------------------------------------------------
NA_PREFS_KEY = r"HKCU\Software\Native Instruments\Native Access"

def default_download_dir(p: Prefix) -> Path:
    """C:\\users\\Public\\Downloads: inside the prefix, next to NA's content location
    (Public\\Documents). Wine turns the per-user Downloads folder into a symlink to
    the host's ~/Downloads, which the Flatpak sandbox mounts read-only; Public
    folders are never symlinked, so this one is always writable."""
    return p.drive_c / "users/Public/Downloads"

def writable_dir(host: Path) -> bool:
    """Can we create a file there? (os.access says yes on a read-only bind mount, so really try.)"""
    try:
        host.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=host, prefix=".vstenv-write-test-"): pass
        return True
    except OSError: return False

def download_location_status(p: Prefix) -> tuple[str, bool]:
    """(NA's configured download location as a Windows path, or '' when unset; writable from here)"""
    cur = p.reg_query(NA_PREFS_KEY).get("DownloadLocation", "")
    return cur, bool(cur) and writable_dir(p.to_host(cur))

def download_location(p: Prefix, reporter=None) -> bool:
    """A fresh prefix has no download location at all (the daemon then fails every
    download with "Download folder does not exist"), and a location on the host's
    ~/Downloads is read-only inside the sandbox ("could not create new file").
    Keep a user-chosen location that works; otherwise point NA at
    default_download_dir. Returns True if the preference was changed."""
    r = null_reporter(reporter)
    r.step("Download location")
    cur, ok = download_location_status(p)
    if ok: r.skip(cur); return False
    d = default_download_dir(p); d.mkdir(parents=True, exist_ok=True)
    win = p.to_win(d)
    # The NTK daemon serves these preferences to NA from memory, read at its start:
    # restart it around the write so NA sees the new value without a prefix reboot.
    # (sc talks to the prefix's services.exe, so this works across Flatpak instances.)
    running = "RUNNING" in p.sc("query", "NTKDaemon")
    if running: p.sc("stop", "NTKDaemon"); time.sleep(2)
    p.reg_add(NA_PREFS_KEY, "DownloadLocation", win)
    if running: p.sc("start", "NTKDaemon")
    r.ok(f"{win} (was {cur}: not writable)" if cur else f"{win} (was unset)")
    return True

# --- asar patching (fixes 6 and 7) -----------------------------------------------------------------------------
def _patch_asar(p: Prefix, edits: dict[str, "callable"], reporter=None) -> dict[str, str]:
    """Rewrite entries of NA's app.asar. edits: {path regex: fn(bytes) -> bytes | None}
    (None = no change). Only the edited entries change; offsets and per-file
    SHA-256 integrity are recomputed, unpacked entries untouched, and the asar
    header hash in the exe's Integrity resource is updated (NA ships with the
    EnableEmbeddedAsarIntegrityValidation fuse on). Returns {regex: patched|unchanged|missing}."""
    asar = na_dir(p) / "resources/app.asar"; exe = na_exe(p)
    data = asar.read_bytes()
    json_len = struct.unpack_from("<I", data, 12)[0]
    hjson = data[16:16 + json_len]; header = json.loads(hjson.rstrip(b"\0"))
    base = 16 + ((json_len + 3) & ~3)
    old_hash = hashlib.sha256(hjson).hexdigest()
    entries = []
    def walk(node, path):
        for k, v in node.get("files", {}).items():
            q = f"{path}/{k}" if path else k
            if "files" in v: walk(v, q)
            elif not v.get("unpacked"): entries.append((q, v))
    walk(header, "")
    BLOCK = 4 * 1024 * 1024
    def integrity(b):
        return {"algorithm": "SHA256", "hash": hashlib.sha256(b).hexdigest(), "blockSize": BLOCK,
                "blocks": [hashlib.sha256(b[i:i + BLOCK]).hexdigest() for i in range(0, len(b), BLOCK)]}
    result = {rx: "missing" for rx in edits}; replaced: dict[str, bytes] = {}
    for q, node in entries:
        for rx, fn in edits.items():
            if re.fullmatch(rx, q):
                o = int(node["offset"]); blob = data[base + o: base + o + node["size"]]
                nb = fn(blob)
                result[rx] = "patched" if nb is not None and nb != blob else "unchanged"
                if nb is not None and nb != blob: replaced[q] = nb
    if not replaced: return result
    entries.sort(key=lambda e: int(e[1]["offset"]))
    chunks, cur = [], 0
    for q, node in entries:
        if q in replaced: blob = replaced[q]; node["integrity"] = integrity(blob)
        else: o = int(node["offset"]); blob = data[base + o: base + o + node["size"]]
        node["offset"] = str(cur); node["size"] = len(blob); chunks.append(blob); cur += len(blob)
    hj = json.dumps(header, separators=(",", ":")).encode("utf-8")
    new_hash = hashlib.sha256(hj).hexdigest()
    pad = ((len(hj) + 3) & ~3) - len(hj)
    head = struct.pack("<IIII", 4, len(hj) + pad + 8, len(hj) + pad + 4, len(hj))
    ed = exe.read_bytes()
    if ed.count(old_hash.encode()) < 1: raise RuntimeError("asar header hash not found in exe — asar/exe mismatch")
    orig = asar.with_suffix(".asar.orig")
    if not orig.exists(): shutil.copy2(asar, orig)
    exb = exe.with_suffix(".exe.pre-asarpatch")
    if not exb.exists(): shutil.copy2(exe, exb)
    tmp = asar.with_suffix(".asar.tmp")
    with open(tmp, "wb") as f:
        f.write(head); f.write(hj); f.write(b"\0" * pad)
        for c in chunks: f.write(c)
    tmp.replace(asar)
    exe.write_bytes(ed.replace(old_hash.encode(), new_hash.encode()))
    return result

RENDERER_RX = r"out/renderer/assets/index-[\w-]+\.js"
MAIN_RX = r"out/main/index\.js"

# --- fix 6: dependency check patch --------------------------------------------------------------------------------
_DEP_PAT = re.compile(rb"(_0x[0-9a-f]+)=(_0x[0-9a-f]+)\[0x0\]\?\?(_0x[0-9a-f]+)\[0x0\],(_0x[0-9a-f]+)=\2\[_0x[0-9a-f]+\(0x[0-9a-f]+\)\]\((_0x[0-9a-f]+)=>\5\[")

# NA >= 3.26.0 rewrote the selector and prefers installed products itself:
# cand = bestInstalled ?? installed[0] ?? deploying ?? owned[0] ?? players[0]
_DEP_NATIVE_PAT = re.compile(rb"(_0x[0-9a-f]+)=(_0x[0-9a-f]+)\?\?(_0x[0-9a-f]+)\[0x0\]\?\?(_0x[0-9a-f]+)\?\?(_0x[0-9a-f]+)\[0x0\]\?\?(_0x[0-9a-f]+)\[0x0\];if\(!\1\)")

def dependency_native(js: bytes) -> bool:
    """True when this renderer already lets an installed viable product satisfy
    the dependency (NA >= 3.26.0), so no patch is needed."""
    return _DEP_NATIVE_PAT.search(js) is not None

def _dep_edit(js: bytes):
    if DEP_MARK.encode() in js or dependency_native(js): return None
    ms = list(_DEP_PAT.finditer(js))
    if len(ms) != 1: raise LookupError(f"patch site not found ({len(ms)} matches)")
    m = ms[0]; cand, owned, players = m.group(1), m.group(2), m.group(3)
    old = b"%s=%s[0x0]??%s[0x0]" % (cand, owned, players)
    new = b"%s=%s%s.find(p=>p.isInstalled)??%s.find(p=>p.isInstalled)??%s[0x0]??%s[0x0]" % (cand, DEP_MARK.encode(), owned, players, owned, players)
    return js.replace(old, new, 1)

def dependency_patch(p: Prefix, reporter=None) -> str:
    """Make an *installed* viable Kontakt satisfy library dependency checks
    (NA <= 3.25 picked the first owned non-Player product and reported the
    installed Player as missing). NA >= 3.26 does this itself: nothing to patch."""
    r = null_reporter(reporter)
    r.step("Patching NA dependency check (installed Player wins)")
    st = dependency_status(p)
    if st == "patched": r.skip("already"); return "already"
    if st == "native": r.skip("built into Native Access 3.26+, no patch needed"); return "native"
    try: res = _patch_asar(p, {RENDERER_RX: _dep_edit}, r)
    except LookupError as e: r.fail(f"{e} — NA version not yet supported"); return "unsupported"
    st = res[RENDERER_RX]
    (r.ok if st == "patched" else r.fail)(st); return st if st == "patched" else "unsupported"

def dependency_status(p: Prefix) -> str:
    """'patched' (our marker present), 'native' (NA >= 3.26 selector), or 'missing'."""
    asar = na_dir(p) / "resources/app.asar"
    if not asar.exists(): return "missing"
    data = asar.read_bytes()
    if DEP_MARK.encode() in data: return "patched"
    return "native" if dependency_native(data) else "missing"

def dependency_patched(p: Prefix) -> bool:
    return dependency_status(p) != "missing"

# --- fix 7: NA self-updater -------------------------------------------------------------------------------------
_AU_ON, _AU_OFF = b"autoUpdateEnabled:!0", b"autoUpdateEnabled:!1"

def _au_edit(js: bytes):
    if _AU_OFF in js: return None
    if js.count(_AU_ON) != 1: raise LookupError(f"autoUpdateEnabled default not found once ({js.count(_AU_ON)})")
    return js.replace(_AU_ON, _AU_OFF, 1)   # same length: build default flips to "auto updates disabled"

def disable_self_update(p: Prefix, reporter=None) -> str:
    """Flip NA's build-config default so the updater is never initialised (NA
    logs "auto updates disabled, skipping init"): no download, no prompt, no
    error toast. Updates happen only through an installer the user downloads
    from NI (install_native_access). The env var can only enable, never
    disable, and the dev.json override schema only picks daemon/log keys."""
    r = null_reporter(reporter)
    r.step("Disabling Native Access self-updates")
    if self_update_disabled(p): r.skip("already"); return "already"
    try: res = _patch_asar(p, {MAIN_RX: _au_edit}, r)
    except LookupError as e: r.fail(f"{e} — NA version not yet supported"); return "unsupported"
    st = res[MAIN_RX]
    (r.ok if st == "patched" else r.fail)("updates only via a downloaded installer" if st == "patched" else st)
    return st if st == "patched" else "unsupported"

def self_update_disabled(p: Prefix) -> bool:
    asar = na_dir(p) / "resources/app.asar"
    return asar.exists() and _AU_OFF in asar.read_bytes()

def version_notice(p: Prefix) -> dict:
    """installed vs latest (from NI's feed, version string only) and whether latest is validated."""
    inst = pe.version(na_exe(p)) if na_exe(p).exists() else None
    inst_short = ".".join(inst.split(".")[:3]) if inst else None
    latest = latest_version()
    newer = bool(inst_short and latest and _vtuple(latest) > _vtuple(inst_short))
    return {"installed": inst_short, "latest": latest, "newer": newer, "latest_known_good": latest in KNOWN_GOOD if latest else False,
            "installed_known_bad": KNOWN_BAD.get(inst_short) if inst_short else None,
            "latest_known_bad": KNOWN_BAD.get(latest) if latest else None}

def _vtuple(v: str): return tuple(int(x) for x in re.findall(r"\d+", v)[:3])

# --- launch / update -------------------------------------------------------------------------------------------
def clear_stale_mutexes(p: Prefix):
    """boost named mutexes are not crash-safe; clear when no NI app runs."""
    if any(p.is_running(x) for x in ("Native Access.exe", "Kontakt", "NTKDaemon.exe")): return
    d = p.drive_c / "ProgramData/boost_interprocess"
    if d.exists():
        for f in d.rglob("*"):
            if f.is_file(): f.unlink(missing_ok=True)

FOREIGN_WINESERVER = ("The prefix is already running under a wineserver this app cannot reach (started in another pid "
                      "namespace, e.g. by a sandboxed DAW with its own Wine). Wine cannot work across that boundary (Native "
                      "Access would crash and every Wine process would hang). Close that program, wait a few seconds for "
                      "its wineserver to exit, then try again.")

def launch(p: Prefix, reporter=None, extra_args=()) -> subprocess.Popen:
    r = null_reporter(reporter)
    exe = na_exe(p)
    if not exe.exists(): raise RuntimeError(f"Native Access is not installed yet — download it from {NA_DOWNLOAD_PAGE} and use install-na")
    if p.wineserver_scope() == "foreign": raise RuntimeError(FOREIGN_WINESERVER)
    if p.is_running("Native Access.exe"): p.kill_exe("Native Access.exe")
    clear_stale_mutexes(p)
    if stack_patch(exe) == "patched": r.log("re-applied stack patch (NA updated itself)")
    download_location(p, r)
    paths.ensure_dirs()
    log = paths.LOGS / "native-access-launch.log"
    # --disable-gpu: NA >= 3.25's GPU process crash-loops under Wine (blank window)
    # --no-sandbox: NA >= 3.26 (Electron 43) cannot start any child process under
    #   Wine with Chromium's Windows sandbox on ("GPU process launch failed:
    #   error_code=39", "Network service crashed", then "GPU process isn't usable.
    #   Goodbye." within a second); harmless on older builds
    return p.spawn([str(exe), "--disable-gpu", "--no-sandbox", *extra_args], log=log)

def pending_update(p: Prefix) -> Path | None:
    f = p.user_dir / "AppData/Local/nativeaccess2-updater/pending/Native-Access-latest.exe"
    return f if f.exists() else None

def apply_pending_update(p: Prefix, reporter=None) -> bool:
    r = null_reporter(reporter)
    f = pending_update(p)
    if not f: return False
    if p.is_running("Native Access.exe"): p.kill_exe("Native Access.exe")
    install_native_access(p, f, r)
    return True

def install_native_access(p: Prefix, installer: Path, reporter=None):
    """Install (or update) NA from an installer the user downloaded from NI, then apply the NA-side fixes."""
    r = null_reporter(reporter)
    installer = Path(installer)
    if not installer.is_file(): raise FileNotFoundError(f"installer not found: {installer}")
    if p.is_running("Native Access.exe"): p.kill_exe("Native Access.exe")
    from ... import runtime, setup
    if not runtime.status(p)["prepared"]: setup.prepare(p, r)
    install(p, installer, r)
    r.step("Patching Native Access.exe stack reserve to 64MB"); r.ok(stack_patch(na_exe(p)))
    disable_self_update(p, r); ntk_daemon(p, r); config(p, r); download_location(p, r); dependency_patch(p, r); p.wait_idle()

def install_native_access_fixes(p: Prefix, reporter=None):
    """Re-apply the NA-side fixes to an already installed Native Access (repair)."""
    r = null_reporter(reporter)
    r.step("Patching Native Access.exe stack reserve to 64MB"); r.ok(stack_patch(na_exe(p)))
    disable_self_update(p, r); ntk_daemon(p, r); config(p, r); download_location(p, r); dependency_patch(p, r); p.wait_idle()

def status(p: Prefix) -> dict:
    exe = na_exe(p)
    return {
        "installed": exe.exists(),
        "version": pe.version(exe) if exe.exists() else None,
        "stack_patch": stack_ok(exe) if exe.exists() else False,
        "ntk_daemon": (p.drive_c / NTK_REL / "NTKDaemon.exe").exists(),
        "ntk_version": pe.version(p.drive_c / NTK_REL / "NTKDaemon.exe") if (p.drive_c / NTK_REL / "NTKDaemon.exe").exists() else None,
        "dependency_patch": dependency_patched(p) if exe.exists() else False,
        "self_update_disabled": self_update_disabled(p) if exe.exists() else False,
        "pending_update": pending_update(p) is not None,
        "running": p.is_running("Native Access.exe"),
    }
