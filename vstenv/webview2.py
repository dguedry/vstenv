"""The Microsoft WebView2 runtime, for apps whose window is an embedded Edge.

Some vendor apps host their UI in Microsoft's WebView2 control (Orchestral
Tools' SINE Player; Audio Modeling's Software Center, a Rust/wry app). Without a
WebView2 runtime present they do not just fail gracefully: they crash at startup
in ole32 on the "Could not find the WebView2 Runtime" path. Installing the
Evergreen runtime into the prefix stops that crash, so the app launches and
opens its window.

Honesty note: this fixes the *crash*, not everything. WebView2 content still
renders poorly or blank under Wine and its text fields do not take keyboard
input (see the README "Known limitation: WebView2 logins"). So this is a
best-effort improvement, launch instead of crash, not full support. It is
installed on demand (a ~150 MB download), only when a WebView2 app is in the
prefix, not for everyone.
"""
from __future__ import annotations

from pathlib import Path

from . import paths
from .download import fetch
from .progress import null_reporter
from .wine import Prefix

# The Evergreen *standalone* installer: the full runtime in one checksummed file,
# so nothing is downloaded from inside Wine (the tiny bootstrapper fetches the
# payload through Wine's networking, which is the fragile part). This is the same
# pinned artifact Bottles' "webview2" dependency installs -- the configuration the
# "works with the Soda runner" reports are based on.
URL = ("https://msedge.sf.dl.delivery.mp.microsoft.com/filestreamingservice/files/"
       "79f5276c-391e-481d-9e0f-50d730ae66a9/MicrosoftEdgeWebView2RuntimeInstallerX64.exe")
SHA256 = "91d82975e73cf4c0b1315679282c9f67e6cdbfeefc0ac6eaf2e68e5be71e982e"
# The runtime registers itself here (the GUID is Microsoft's WebView2 client id).
CLIENT_KEY = r"HKLM\Software\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"

def installed(p: Prefix) -> bool:
    """True if a WebView2 runtime is registered in the prefix."""
    try:
        return bool(p.reg_query(CLIENT_KEY).get("pv"))
    except Exception:
        return False

# The WebView2 runtime installs itself under here; its own folders are not "apps".
_RUNTIME_DIRS = ("Microsoft/EdgeWebView", "Microsoft/EdgeCore", "Microsoft/EdgeUpdate")

def apps_present(p: Prefix) -> list[str]:
    """Top-level installed programs (not the runtime itself) whose main exe hosts a
    WebView2 control. Matched by the exe named like the program dir carrying the
    marker, or a bundled BrowserRuntime, so we do not flag every dir that merely
    mentions the string."""
    out = []
    for base in ("Program Files", "Program Files (x86)"):
        root = p.drive_c / base
        if not root.is_dir(): continue
        for d in root.iterdir():                       # vendor dirs, one level down
            if not d.is_dir(): continue
            rel = str(d.relative_to(p.drive_c)).replace("\\", "/")
            if any(part in rel for part in _RUNTIME_DIRS): continue
            for sub in [d, *[x for x in d.rglob("*") if x.is_dir() and len(x.relative_to(d).parts) <= 2]]:
                if any(r in str(sub) for r in _RUNTIME_DIRS): continue
                if (sub / "BrowserRuntime/msedgewebview2.exe").exists():
                    out.append(sub.name); break
                # an exe in this dir that hosts WebView2 (its name need not match the dir)
                if any(_exe_uses_webview2(exe) for exe in sub.glob("*.exe")):
                    out.append(sub.name); break
    return sorted(set(out))

def _exe_uses_webview2(exe: Path) -> bool:
    try: b = exe.read_bytes()
    except OSError: return False
    return b"CoreWebView2" in b or b"msedgewebview2" in b or b"WebView2Loader" in b

# Chromium presents WebView2 content through DirectComposition and repaints only
# each frame's damaged region, trusting the swapchain to preserve the rest;
# neither assumption holds under Wine with DXVK, so half-drawn regions alternate
# and embedded video churns (the Audio Modeling catalog, SINE's tips video).
# These flags make the browser present full frames through a plain swapchain.
# They can only be delivered through WebView2's per-app policy registry key:
# the documented WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS variable is ignored
# under Wine, where every process counts as elevated.
PRESENTATION_FLAGS = "--disable-direct-composition --ui-disable-partial-swap"
POLICY_KEY = r"HKLM\Software\Policies\Microsoft\Edge\WebView2\AdditionalBrowserArguments"

def host_exes(p: Prefix) -> list[str]:
    """Exe names of installed programs that host a WebView2 control -- the policy
    key's per-app value names. The exe carrying the marker, or (next to a bundled
    BrowserRuntime) every exe but the uninstaller. A PLUGIN that hosts WebView2
    (SINE Player's VST3) runs inside yabridge's host processes, so those exe
    names are included when such a plugin is in the prefix."""
    out = set()
    for base in ("Program Files", "Program Files (x86)"):
        root = p.drive_c / base
        if not root.is_dir(): continue
        for d in root.iterdir():
            if not d.is_dir(): continue
            rel = str(d.relative_to(p.drive_c)).replace("\\", "/")
            if any(part in rel for part in _RUNTIME_DIRS): continue
            for sub in [d, *[x for x in d.rglob("*") if x.is_dir() and len(x.relative_to(d).parts) <= 2]]:
                if any(r in str(sub) for r in _RUNTIME_DIRS): continue
                exes = list(sub.glob("*.exe"))
                out.update(e.name for e in exes if _exe_uses_webview2(e))
                if (sub / "BrowserRuntime/msedgewebview2.exe").exists():
                    out.update(e.name for e in exes if not e.name.lower().startswith("unins"))
    vst3 = p.drive_c / "Program Files/Common Files/VST3"
    if vst3.is_dir() and any(f.is_file() and _exe_uses_webview2(f) for f in vst3.rglob("*.vst3")):
        out.update(("yabridge-host.exe", "yabridge-host-32.exe"))
    return sorted(out)

def apply_presentation_flags(p: Prefix, reporter=None) -> int:
    """Give every WebView2 host the presentation flags, where no per-app value
    exists yet (a vendor module that needs different flags owns its own value)."""
    r = null_reporter(reporter)
    exes = host_exes(p)
    if not exes: return 0
    try: have = p.reg_query(POLICY_KEY)
    except Exception: have = {}
    missing = [x for x in exes if x not in have]
    if not missing: return 0
    r.step("WebView2 apps: stable frame presentation")
    n = 0
    for exe in missing:
        cp = p.run(["reg", "add", POLICY_KEY, "/v", exe, "/t", "REG_SZ",
                    "/d", PRESENTATION_FLAGS, "/f"], timeout=120)
        if cp.returncode == 0: n += 1
    (r.ok if n == len(missing) else r.fail)(", ".join(missing))
    return n

def needed(p: Prefix) -> bool:
    """A WebView2 app is present but no runtime is installed."""
    return not installed(p) and bool(apps_present(p))

def install(p: Prefix, reporter=None, force=False) -> bool:
    """Install the WebView2 runtime into the prefix. Idempotent. Returns True if it
    is present afterwards. Prevents the ole32 startup crash of WebView2 apps; does
    not make their content render or accept keys (a Wine limitation)."""
    r = null_reporter(reporter)
    r.step("Microsoft WebView2 runtime (for embedded-Edge apps)")
    if installed(p) and not force:
        r.skip("already"); return True
    exe = fetch(URL, paths.DOWNLOADS / "MicrosoftEdgeWebView2RuntimeInstallerX64.exe",
                sha256=SHA256, reporter=r, label="WebView2 runtime (193 MB)")
    dst = p.user_dir / "MicrosoftEdgeWebView2Setup.exe"
    try:
        import shutil; dst.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(exe, dst)
    except Exception as e:
        r.fail(f"could not stage the installer: {str(e)[:80]}"); return False
    win = rf"C:\users\{p.user_dir.name}\MicrosoftEdgeWebView2Setup.exe"
    cp = p.run([win, "/silent", "/install"], timeout=1200)
    try: dst.unlink()
    except OSError: pass
    if not installed(p):
        r.fail(f"installer exit {cp.returncode}; runtime not registered"); return False
    r.ok(p.reg_query(CLIENT_KEY).get("pv", "installed") + " (apps launch; WebView2 UI may still not render under Wine)")
    return True

def status(p: Prefix) -> dict:
    return {"installed": installed(p), "apps": apps_present(p)}
