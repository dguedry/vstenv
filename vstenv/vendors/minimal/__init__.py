"""Minimal Audio: the Minimal Hub and the plugins it installs.

The Hub's Windows download is not an installer. "Minimal Hub.exe" is the
application itself, a Tauri 2 program that draws its whole window through
Microsoft's WebView2 control (an embedded Edge) and keeps itself up to date
in place. Picked under "Install Windows software", the generic path would run
it as a setup and wait for it to exit, so this module recognises the file and
installs it as a program instead: copied into Program Files, listed on the
Programs page, given a desktop menu entry (menu_entry: unlike the other
managers it needs nothing at launch, and the entry starts it through `vstenv
run` anyway) -- and, mainly, put where the WebView2 handling can see it.

That handling is the point. Every WebView2 host in Program Files gets the
full-frame presentation flags through WebView2's policy registry key (see
webview2.py), keyed by exe name; run from the Downloads folder the Hub got
none, and its window came up solid black: Chromium loaded the page ("Frontend
signaled ready" in its own log) but never painted a frame. With the flags it
stayed blank, a dark grey. Only software GL paints it, on hardware (NVIDIA on
X11) where Audio Modeling's Center renders fine with hardware GL -- so this
module forces the software-GL flags for the Hub's exe alone, rather than
asking everyone to switch the prefix-wide "Software rendering for WebView2
apps" setting on. The Hub is a sign-in and download window; software
rendering costs it nothing noticeable.

Its plugins install to the standard VST3 and VST2 folders and are bridged as
they appear while the Hub runs (from the app or its menu entry, both go
through `vstenv run`) and again when it closes, as for any manager.
"""
from __future__ import annotations

import re, shutil
from pathlib import Path

from .. import Vendor, Product, Note, Check
from ...wine import Prefix

MANAGER = "Minimal Hub"
HUB_EXE = "Minimal Hub.exe"
HUB_DIR = Path("Program Files/Minimal Audio/Minimal Hub")
# Where the Hub puts each product's own folder (its support site's documented path).
PRODUCTS_DIR = Path("Program Files/Minimal")
# A Tauri program carries its IPC bridge's name; an installer that merely wraps
# the Hub (should Minimal ever ship one) would not.
_APP_MARKER = b"__TAURI_INTERNALS__"
_POLICY_KEY = r"HKLM\Software\Policies\Microsoft\Edge\WebView2\AdditionalBrowserArguments"


def is_hub_app(f: Path) -> bool:
    """The Hub's download is the program itself, not a setup for it."""
    try:
        return _APP_MARKER in Path(f).read_bytes()
    except OSError:
        return False


class MinimalAudio(Vendor):
    id = "minimal"
    name = "Minimal Audio"
    publisher = re.compile(r"^minimal(\s*audio)?$", re.I)
    manager_name = MANAGER
    menu_entry = True          # nothing happens at launch; its fixes are registry settings
    download_page = "https://www.minimal.audio/"
    installer_hint = "the Minimal Hub download for Windows (Minimal Hub.exe)"

    def _hub_exe(self, p: Prefix) -> Path:
        return p.drive_c / HUB_DIR / HUB_EXE

    # -- the manager -------------------------------------------------------------------
    def manager_installed(self, p): return self._hub_exe(p).is_file()
    def manager_version(self, p):
        from ... import pe
        return pe.version(self._hub_exe(p)) if self.manager_installed(p) else None
    def manager_exe_names(self): return (HUB_EXE,)

    def browser_window_exes(self):
        """The Hub's window is a WebView2 control: Chromium draws it and wants
        DXVK, not the DirectComposition patches."""
        return (HUB_EXE,)

    def accepts_manager_installer(self, f):
        return bool(re.search(r"^minimal[ _-]?hub.*\.exe$", Path(f).name, re.I))

    def install_manager(self, p, installer, r=None):
        """Put the Hub in Program Files. Its download is the program itself, so
        it is copied into place; a real setup, if Minimal ever ships one, is run
        like any other installer."""
        from ...progress import null_reporter
        rr = null_reporter(r)
        installer = Path(installer)
        if not is_hub_app(installer):
            from ... import programs
            programs.install(p, installer, rr)
            return
        rr.step(f"Installing {MANAGER} as a program")
        dst = self._hub_exe(p)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(installer, dst)
        rr.ok(f"{self.manager_version(p) or 'copied'} -> {p.to_win(dst.parent)}")
        self.prepare(p, rr)      # its flags must be in place before its first launch

    def launch_manager(self, p, r=None, args=()):
        """No launch fixes of its own (the policy value does the work), so the
        generic start is right -- asked for by source: programs.run hands a
        "vendor"-sourced manager back here, which would never end."""
        from ... import programs
        m = self._program(p, source=self.id)
        if m is None:
            raise RuntimeError(f"{MANAGER} is not installed yet — download it from {self.download_page} "
                               "and pick the file under Install")
        return programs.run(p, m, r)

    def _program(self, p: Prefix, source="vendor"):
        if not self.manager_installed(p): return None
        from ...programs import Program
        exe = self._hub_exe(p)
        return Program(name=MANAGER, version=self.manager_version(p) or "", publisher=self.name,
                       exe=p.to_win(exe), install_dir=p.to_win(exe.parent), sources=[source])

    def programs(self, p):
        """The Hub registers nothing in the registry and writes no shortcut; this
        is how the Programs page and the desktop menu learn of it."""
        m = self._program(p)
        return [m] if m else []

    # -- the prefix ------------------------------------------------------------------
    def flags(self, p) -> str:
        """The presentation flags every WebView2 host gets, plus software GL
        whether or not the prefix-wide setting is on (see the module docstring)."""
        from ... import webview2
        f = webview2.presentation_flags(p)
        if webview2.SOFTWARE_GL_FLAGS not in f: f += " " + webview2.SOFTWARE_GL_FLAGS
        return f

    def prepare(self, p, r=None):
        """Force the Hub's policy value: webview2.apply_presentation_flags only
        fills in hosts that have none, and would leave the Hub on hardware GL."""
        if not self.manager_installed(p): return
        from ...progress import null_reporter
        rr = null_reporter(r)
        rr.step(f"{MANAGER}: software rendering for its WebView2 window")
        cp = p.run(["reg", "add", _POLICY_KEY, "/v", HUB_EXE, "/t", "REG_SZ", "/d", self.flags(p), "/f"],
                   timeout=120)
        (rr.ok if cp.returncode == 0 else rr.fail)(
            "WebView2 policy flags" if cp.returncode == 0 else f"reg exit {cp.returncode}")

    def after_install(self, p, r=None):
        self.prepare(p, r)

    # -- what is installed ---------------------------------------------------------------
    def products(self, p):
        """Each product's folder under Program Files/Minimal, and anything the
        registry attributes to Minimal that has no folder there."""
        from ... import programs
        out, seen = [], set()
        d = p.drive_c / PRODUCTS_DIR
        if d.is_dir():
            for sub in sorted(d.iterdir()):
                if not sub.is_dir(): continue
                seen.add(sub.name.lower())
                out.append(Product(name=sub.name, vendor=self.id, kind="Plugin", install_dir=p.to_win(sub)))
        for x in programs.installed(p):
            if not self.publisher.search(x.publisher or "") or self.is_manager_program(x): continue
            base = re.sub(r"\s+[\d.]+$", "", x.name).strip() or x.name
            if base.lower() in seen: continue
            seen.add(base.lower())
            out.append(Product(name=base, vendor=self.id, kind="Plugin", version=x.version, install_dir=x.install_dir))
        return sorted(out, key=lambda x: x.name.lower())

    def logs(self, p):
        return {"minimal-hub.log": p.user_dir / "AppData/Local/Minimal/Hub/logs/minimal-hub.log"}

    def product_notes(self, p):
        return [Note(MANAGER, "patched",
                     "A Tauri app whose window is a WebView2 (embedded Edge) control. Its download is the program "
                     "itself, so the app installs it as one. Its window stays black under Wine even with the "
                     "presentation flags every WebView2 host gets, so the app switches this one program to "
                     "software rendering through WebView2's policy key; sign-in and downloads then draw normally.")]

    # -- health ---------------------------------------------------------------------------
    # -- did the Hub's installs land? --------------------------------------------------------
    _DOWNLOAD = re.compile(r"Download: finished .*[\\/]([^\\/]+)\.exe \(\d+ bytes\)")

    def hub_installs(self, p: Prefix) -> list[str]:
        """Product names the Hub's own log says it downloaded and installed."""
        log = self.logs(p)["minimal-hub.log"]
        try: text = log.read_text(errors="replace")
        except OSError: return []
        out = []
        for m in self._DOWNLOAD.finditer(text):
            name = m.group(1)
            if name.lower() == "hub" or name.lower().startswith("hub_"): continue     # its own update
            if name not in out: out.append(name)
        return out

    @staticmethod
    def _norm(s: str) -> str: return re.sub(r"[^a-z0-9]", "", s.lower())

    def missing_installs(self, p: Prefix) -> list[str]:
        """Products the Hub reported installed with nothing of theirs in the
        prefix: the Hub installs through PowerShell, and before the app's
        replacement carried Start-Process out (v0.1.121) the install was
        reported done with nothing run and the download deleted."""
        present = set()
        for d in (p.drive_c / "Program Files/Common Files/VST3", p.drive_c / "Program Files/VstPlugins",
                  p.drive_c / PRODUCTS_DIR):
            if d.is_dir(): present.update(self._norm(x.stem) for x in d.iterdir())
        return [n for n in self.hub_installs(p) if self._norm(n) not in present]

    def checks(self, p):
        c = [Check(f"{MANAGER} installed", self.manager_installed(p), self.manager_version(p) or "",
                   fix=f"download it from {self.download_page}, then pick the file under Install")]
        if self.manager_installed(p):
            try: have = p.reg_query(_POLICY_KEY).get(HUB_EXE, "")
            except Exception: have = ""
            from ... import webview2
            ok = webview2.SOFTWARE_GL_FLAGS in have
            c.append(Check(f"{MANAGER} software rendering", ok,
                           "set through the WebView2 policy key" if ok else "its window will come up black",
                           fix="re-run setup"))
            missing = self.missing_installs(p)
            if self.hub_installs(p):
                c.append(Check(f"{MANAGER} installs landed", not missing,
                               "every product the Hub installed is in the prefix" if not missing else
                               f"the Hub reported these installed but nothing of theirs is here: {', '.join(missing)}. "
                               "An app older than 0.1.121 let the Hub's install script report success without running it. "
                               "Update the app, open it once (it applies the update's fixes), then reinstall them in the Hub.",
                               fix="update the app, open it, then reinstall the products in Minimal Hub"))
        return c


VENDOR = MinimalAudio()
