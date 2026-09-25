"""InstallAware setups (a stub exe carrying an MSI and a FileBag of payload).

Several vendors ship these; Native Instruments' "<Product> Setup PC.exe" is the
one this was written against. Under Wine they may fail, or stop dead: payload
extracted, then zero CPU forever, querying an MSI virtual table Wine's SQL
parser rejects. So a setup is run silently under an MSI trace, and if it does
not succeed the trace gives every payload -> destination root and the registry
keys, and the payload is deployed by hand from the FileBag. An install a
vendor's manager started and left half-done leaves the extracted installer in
the user's Temp folder; that is enough to finish it the same way.
"""
from __future__ import annotations

import os, re, shutil, subprocess, tempfile, time, zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .. import paths, host
from ..progress import null_reporter
from ..wine import Prefix

TRACE_RE = re.compile(r"VALUES \( '(P[0-9A-F]+)_(\d+)' , '([^']*)'")
SKIP_ROOTS = ("Installer Log", "Start Menu", "\\Desktop", "Avid\\Audio", "ProgramData\\Native Instruments")
SETUP_RE = re.compile(r"\s+Setup PC\.exe$", re.I)

def is_setup(f: Path) -> bool:
    """A '<Product> Setup PC.exe', or the .zip a manager downloads that wraps one."""
    f = Path(f)
    if SETUP_RE.search(f.name): return True
    if f.suffix.lower() == ".zip" and f.is_file():
        try:
            with zipfile.ZipFile(f) as z: return any(SETUP_RE.search(n) for n in z.namelist())
        except (OSError, zipfile.BadZipFile): return False
    return False

def parse_trace(trace: Path) -> tuple[dict[str, str], dict[str, str]]:
    """(roots: 'P<hash>_1' -> windows dir, regkeys: 'SOFTWARE\\...\\Value' -> data)"""
    props: dict[str, dict[int, str]] = {}
    with open(trace, errors="ignore") as f:
        for line in f:
            for m in TRACE_RE.finditer(line):
                props.setdefault(m.group(1), {})[int(m.group(2))] = m.group(3).replace("\\\\", "\\")
    roots, regkeys = {}, {}
    last_key = ""
    for bag, v in props.items():
        if v.get(2) == "REGISTRY KEYS" and 3 in v and 4 in v and not v[3].startswith("HKCU"):
            name = v[3]
            # a bare value name (e.g. 'ContentVersion') belongs to the product key that
            # the previous REGISTRY KEYS entry addressed (SOFTWARE\Vendor\<Product>)
            if "\\" not in name:
                if not last_key: continue
                name = last_key + "\\" + name
            regkeys[name] = v[4]; last_key = name.rpartition("\\")[0]
        dest = v.get(1, "")
        if dest[:3].upper() == "C:\\" and not any(x in dest for x in SKIP_ROOTS):
            roots[bag + "_1"] = dest.rstrip("\\")
    return roots, regkeys

def setup_exe_from(path: Path, workdir: Path) -> Path:
    """Accept a 'X Setup PC.exe' or the .zip that wraps it."""
    path = Path(path)
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as z:
            names = [n for n in z.namelist() if n.lower().endswith(".exe")]
            if not names: raise RuntimeError("zip contains no setup exe")
            z.extract(names[0], workdir); return workdir / names[0]
    return path

def run_watched(p: Prefix, exe: Path, trace: Path, r) -> tuple[int, bool]:
    """Run a setup silently, giving up early if it stops making progress. Returns
    (exit code, stalled). A stalled setup is killed so the staged payload it
    holds is free for the deploy fallback."""
    from ..stall import StallWatch
    watch = StallWatch()
    with open(trace, "wb") as f:
        proc = host.popen([str(p.build.wine), str(exe), "/s"], env=p.wine_env(None, "+msi"), stdout=subprocess.DEVNULL, stderr=f)
        deadline = time.time() + 3600
        while True:
            try: return proc.wait(timeout=10), False
            except subprocess.TimeoutExpired: pass
            if watch.update([pid for pid, _ in p.processes(exe.name)]):
                r.log(f"no CPU and no disk writes for {watch.quiet_for():.0f}s: treating as stalled")
                p.kill_exe(exe.name)
                try: proc.wait(timeout=30)
                except subprocess.TimeoutExpired: proc.kill()
                return -1, True
            if time.time() > deadline:
                p.kill_exe(exe.name); proc.kill()
                return -1, True

def install(p: Prefix, setup: Path, reporter=None, keep_trace=False) -> dict:
    """Install from a setup exe (or its zip). Returns a summary dict."""
    r = null_reporter(reporter); paths.ensure_dirs()
    if not shutil.which("7z"): raise RuntimeError("missing host tool: 7z")
    work = Path(tempfile.mkdtemp(prefix="vstenv-app-", dir=paths.CACHE))
    try:
        exe = setup_exe_from(setup, work)
        name = SETUP_RE.sub("", exe.name)
        trace = paths.LOGS / f"{name}-install.trace"
        r.step(f"Running installer silently: {exe.name}")
        t0 = time.time()
        rc, stalled = run_watched(p, exe, trace, r)
        roots, regkeys = parse_trace(trace)
        installed_ok = (not stalled) and rc == 0 and (any(p.reg_query(_split_key(k)[0]).get(_split_key(k)[1]) for k in regkeys) if regkeys else True)
        if installed_ok:
            r.ok(f"installer succeeded ({time.time()-t0:.0f}s)")
            if not keep_trace: trace.unlink(missing_ok=True)
            return {"name": name, "method": "installer", "regkeys": regkeys}
        if stalled: r.fail("installer stopped making progress; falling back to manual deploy")
        else:       r.fail(f"installer exit {rc}; falling back to manual deploy")
        if not roots: raise RuntimeError("trace has no destination roots; cannot deploy")
        r.step("Extracting installer payload"); bag = work / "bag"; bag.mkdir()
        subprocess.run(["7z", "x", "-y", f"-o{bag}", str(exe)], check=True, capture_output=True)
        msis = list(bag.glob("*.msi"))
        if not msis: raise RuntimeError("no inner MSI in installer")
        r.ok(msis[0].name)
        n = _deploy_and_register(p, msis[0], bag / "data", roots, regkeys, r)
        if not keep_trace: trace.unlink(missing_ok=True)
        return {"name": name, "method": "deploy", "files": n, "regkeys": regkeys}
    finally:
        shutil.rmtree(work, ignore_errors=True)

def _deploy_and_register(p: Prefix, msi: Path, bag: Path, roots: dict, regkeys: dict, r) -> int:
    n = deploy_payload(p, msi, bag, roots, r)
    r.step("Writing registry keys")
    vals = {}
    for k, v in regkeys.items():
        key, val = _split_key(k)
        if not key: r.log(f"skipping registry entry without a key: {k}"); continue
        vals.setdefault("HKLM\\" + key, {})[val] = ("REG_SZ", v)
    if vals: p.reg_import_values(vals, "vstenv-app-install.reg")
    r.ok(f"{sum(len(x) for x in vals.values())} value(s)")
    return n

# --- installs a manager started and did not finish --------------------------------------------------
# The manager runs these setups itself. Under Wine one can remove the previous
# version's files and then die before deploying the new ones, leaving the extracted
# installer (stub exe, inner MSI and the FileBag) in the user's Temp folder. That
# folder is the evidence and, with the download the manager kept, enough to finish.
@dataclass
class StagedInstall:
    name: str            # product, from "<name> Setup PC.exe"
    dir: Path            # the mia*.tmp folder
    exe: Path
    msi: Path
    download: Path | None = None   # the installer the manager downloaded, if still around

FindDownload = Callable[[Prefix, str], "Path | None"]

def staged_installs(p: Prefix, find_download: FindDownload | None = None) -> list[StagedInstall]:
    temp = p.user_dir / "AppData/Local/Temp"
    out = []
    if not temp.is_dir(): return out
    for d in sorted(temp.glob("mia*.tmp")):
        exes = list(d.glob("* Setup PC.exe")); msis = list((d / "data").glob("*.msi")) or list(d.glob("*.msi"))
        if not exes or not msis or not (d / "data").is_dir(): continue
        name = SETUP_RE.sub("", exes[0].name)
        out.append(StagedInstall(name, d, exes[0], msis[0], find_download(p, name) if find_download else None))
    return out

SETUP_HINT = "Setup PC.exe"     # what a running setup's command line contains

def stalled_setup(p: Prefix, watch=None, quiet_seconds: float = 300.0):
    """A setup the manager started that has stopped making progress. Keep the
    returned watch and pass it back on the next call: the judgement needs
    history, not a single sample. Returns (watch, stalled, pids)."""
    from ..stall import StallWatch
    pids = [pid for pid, cmd in p.processes() if SETUP_HINT.lower() in cmd.lower()]
    if watch is None: watch = StallWatch(quiet_seconds=quiet_seconds)
    return watch, watch.update(pids), pids

def rescue(p: Prefix, reporter=None, find_download: FindDownload | None = None, busy: Callable[[Prefix, str], bool] | None = None) -> list[dict]:
    """Kill a stalled setup and finish the install from what it already staged.
    The manager drives these, so when one wedges nothing else can complete it:
    the staged folder is held open by the dead-in-the-water process."""
    r = null_reporter(reporter)
    killed = []
    for pid, cmd in p.processes():
        if SETUP_HINT.lower() in cmd.lower():
            r.step(f"Stopping the stalled installer (pid {pid})")
            try: os.kill(pid, 9); killed.append(pid); r.ok()
            except OSError as e: r.fail(str(e))
    if killed: time.sleep(2)
    return finish_staged(p, r, find_download, busy)

def finish_staged(p: Prefix, reporter=None, find_download: FindDownload | None = None, busy: Callable[[Prefix, str], bool] | None = None) -> list[dict]:
    """Complete every install left half-done. Prefers re-running the installer the
    manager downloaded (install(), with its deploy fallback); without it, runs the
    staged stub under an MSI trace to learn the destination roots and deploys the
    staged FileBag. `busy(p, name)` says whether the manager or that setup still runs."""
    r = null_reporter(reporter)
    results = []
    for st in staged_installs(p, find_download):
        if p.is_running(f"{st.name} Setup") or (busy and busy(p, st.name)):
            r.step(f"Finishing {st.name} install"); r.skip("its installer or manager is running; try again after it exits"); continue
        try:
            if st.download is not None:
                r.log(f"{st.name}: using the installer the manager downloaded ({st.download.name})")
                res = install(p, st.download, r)
            else:
                res = _finish_from_staged(p, st, r)
            shutil.rmtree(st.dir, ignore_errors=True)
            results.append(res)
        except Exception as e:
            r.step(f"Finishing {st.name} install"); r.fail(str(e)[:120])
    return results

def _finish_from_staged(p: Prefix, st: StagedInstall, r) -> dict:
    paths.ensure_dirs()
    trace = paths.LOGS / f"{st.name}-finish.trace"
    r.step(f"Re-running staged installer: {st.exe.name}")
    cp = p.run([str(st.exe), "/s"], debug="+msi", timeout=3600, capture=False, stderr_to=trace)
    roots, regkeys = parse_trace(trace)
    if cp.returncode == 0 and regkeys and any(p.reg_query(_split_key(k)[0]).get(_split_key(k)[1]) for k in regkeys):
        r.ok("installer succeeded"); trace.unlink(missing_ok=True)
        return {"name": st.name, "method": "installer", "regkeys": regkeys}
    r.fail(f"installer exit {cp.returncode}; deploying the staged payload")
    if not roots: raise RuntimeError("trace has no destination roots; cannot deploy")
    n = _deploy_and_register(p, st.msi, st.dir / "data", roots, regkeys, r)
    trace.unlink(missing_ok=True)
    return {"name": st.name, "method": "deploy", "files": n, "regkeys": regkeys}

def _split_key(k: str) -> tuple[str, str]:
    key, _, val = k.rpartition("\\"); return key, val

def deploy_payload(p: Prefix, msi_path: Path, bag: Path, roots: dict[str, str], reporter=None) -> int:
    """Copy FileBag payload into the prefix using the MSI's File->Component->Directory tables."""
    from ..msi import Msi
    r = null_reporter(reporter)
    r.step("Deploying payload files")
    m = Msi(msi_path)
    dirs = {d["Directory"]: (d["Directory_Parent"], d["DefaultDir"]) for d in m.rows("Directory")}
    comp_dir = {c["Component"]: c["Directory_"] for c in m.rows("Component")}
    long = lambda part: part.split("|", 1)[-1]
    cache: dict[str, tuple[str, str] | None] = {}
    def resolve(key):
        if key in cache: return cache[key]
        res = None
        if key in roots: res = (str(p.to_host(roots[key])), "")
        elif key in dirs:
            parent, dd = dirs[key]
            if parent and key != "TARGETDIR":
                base = resolve(parent)
                if base is not None:
                    tpart, spart = dd.split(":", 1) if ":" in dd else (dd, dd)
                    t, s = long(tpart), long(spart)
                    res = (base[0] if t == "." else os.path.join(base[0], t), base[1] if s == "." else os.path.join(base[1], s))
        cache[key] = res; return res
    copied = missing = 0
    for f in m.rows("File"):
        d = comp_dir.get(f["Component_"]); res = resolve(d) if d else None
        if res is None: continue
        tdir, sdir = res; name = long(f["FileName"])
        src = Path(bag) / sdir / name; dst = Path(tdir) / name
        if not src.exists(): missing += 1; continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not dst.exists() or dst.stat().st_size != src.stat().st_size: shutil.copy2(src, dst)
        copied += 1
    r.ok(f"{copied} files" + (f", {missing} missing" if missing else ""))
    return copied
