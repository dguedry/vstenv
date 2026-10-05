"""Audio Modeling: the Software Center and the SWAM instruments it installs.

The Software Center is a WebView2 app; it runs (and its sign-in types) thanks to
the WebView2 runtime, the patched ole32.dll and DXVK's dummy composition
swapchain, so it needs no launch wrapper and keeps its desktop menu entry. Two
things do fail under Wine, and this module carries the fix for both:

* Its product installs. The Center downloads a SWAM instrument's BitRock
  installer and spawns it with `"--mode unattended"` as ONE argument (quoted
  space and all); InstallBuilder does not parse that under Wine, falls back to
  its GUI mode, which cannot start here (an "unknown mode" error, screen
  flicker, the pointer vanishing), and exits 1 -- whereupon the Center deletes
  the download. So while the Center runs, a watcher (Vendor.watch_program)
  hard-links every installer it downloads into a rescue folder the Center's
  cleanup cannot touch, and once the Center's own attempt has failed and been
  cleaned up, finishes the install from the rescue copy in the installer's
  documented unattended mode. Leftovers are also picked up by "Finish
  interrupted installs" and `vstenv finish-installs`.

* Its window constantly flashes half-drawn sections. Chromium presents through
  DirectComposition and repaints only the damaged region of each frame,
  trusting the swapchain to keep the rest -- under DXVK that trust is
  misplaced, so the two buffers alternate between different half-updated
  frames. --disable-direct-composition (present through a plain HWND
  swapchain) plus --ui-disable-partial-swap (always redraw the full frame)
  makes the window pixel-stable and the TRY videos play (measured: zero
  changed frames over 4s idle). WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS is
  ignored here (Wine processes count as elevated, and elevated apps ignore
  flags from the local device environment), but WebView2's policy registry key
  is honored, so prepare() sets the flags there for the Center's exe.
"""
from __future__ import annotations

import os, re, shutil, time
from pathlib import Path

from .. import Vendor, Product, Note
from ...wine import Prefix

_INSTALLER = re.compile(r"-windows(-x64)?-installer\.exe$", re.I)

def _pretty(name: str) -> str:
    """'SWAMDoubleReeds' -> 'SWAM Double Reeds' (installer file names squeeze the
    product name; notifications should not)."""
    return re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])|(?<=[a-z0-9])(?=[A-Z])", " ", name)
CENTER_EXE = "Audio Modeling Software Center.exe"
# WebView2 reads per-app browser arguments from this policy key (value name = exe).
_POLICY_KEY = r"HKLM\Software\Policies\Microsoft\Edge\WebView2\AdditionalBrowserArguments"

class AudioModeling(Vendor):
    id = "am"
    name = "Audio Modeling"
    publisher = re.compile(r"audio\s*modeling", re.I)
    # No manager_name on purpose: the Software Center needs no launch fixes, and a
    # vendor manager would lose its desktop menu entry (vendors.run_through_app).

    def browser_window_exes(self):
        """The Center's window is a WebView2 control; the SWAM instruments draw
        their own JUCE windows, even though they ship WebView2 support too."""
        return (CENTER_EXE,)

    def _products_dir(self, p: Prefix) -> Path:
        return p.drive_c / "Program Files/Audio Modeling"

    def _temp_dir(self, p: Prefix) -> Path:
        return p.user_dir / "AppData/Roaming/Audio Modeling/Software Center/temp"

    def _rescue_dir(self, p: Prefix) -> Path:
        return p.user_dir / "AppData/Local/vstenv/am-rescue"

    def _installed(self, p: Prefix) -> set[str]:
        d = self._products_dir(p)
        if not d.is_dir(): return set()
        return {x.name.replace(" ", "").lower() for x in d.iterdir() if x.is_dir()}

    def products(self, p):
        out = []
        d = self._products_dir(p)
        if not d.is_dir(): return out
        for sub in sorted(d.iterdir()):
            if sub.is_dir() and sub.name != "Software Center":
                out.append(Product(name=sub.name, vendor=self.id, kind="Instrument",
                                   install_dir=str(sub)))
        return out

    FLAGS = "--disable-direct-composition --ui-disable-partial-swap"   # = webview2.PRESENTATION_FLAGS

    def prepare(self, p, r=None):
        """Make the Center present full frames through a plain swapchain (see the
        module docstring). webview2.apply_presentation_flags covers hosts without
        a value; this one FORCES the value, so a stale flag set by an older
        version of this app is corrected rather than kept."""
        if not (self._products_dir(p) / "Software Center").is_dir(): return
        from ...progress import null_reporter
        rr = null_reporter(r)
        rr.step("Software Center: stable frame presentation")
        cp = p.run(["reg", "add", _POLICY_KEY, "/v", CENTER_EXE,
                    "/t", "REG_SZ", "/d", self.FLAGS, "/f"], timeout=120)
        (rr.ok if cp.returncode == 0 else rr.fail)(
            "WebView2 policy flags" if cp.returncode == 0 else f"reg exit {cp.returncode}")

    # -- finishing the Software Center's failed installs ----------------------------------
    def staged_installs(self, p: Prefix) -> list:
        """Installers the Center downloaded whose product is not installed, in its
        temp folder or rescued from it: [(name, installer path)]."""
        installed = self._installed(p)
        t, rd = self._temp_dir(p), self._rescue_dir(p)
        candidates = sorted(t.glob("*/*.exe")) if t.is_dir() else []
        candidates += sorted(rd.glob("*.exe")) if rd.is_dir() else []
        seen, out = set(), []
        for exe in candidates:
            if not _INSTALLER.search(exe.name) or exe.name in seen: continue
            seen.add(exe.name)
            # "SWAMViolin-3.12.3-2944-windows-x64-installer.exe" -> "swamviolin"
            product = exe.name.split("-", 1)[0]
            if product.lower() in installed: continue
            out.append((product, exe))
        return out

    def _rescue_snapshot(self, p: Prefix):
        """Hard-link each downloaded installer out of the Center's temp folder (same
        filesystem: instant, and the Center's cleanup cannot take the data away)."""
        t = self._temp_dir(p)
        if not t.is_dir(): return
        for exe in t.glob("*/*.exe"):
            if not _INSTALLER.search(exe.name): continue
            dst = self._rescue_dir(p) / exe.name
            if dst.exists(): continue
            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                os.link(exe, dst)
            except OSError:
                try: shutil.copy2(exe, dst)
                except OSError: pass

    def _rescue_clean(self, p: Prefix):
        """Drop rescue copies whose product made it in (however it got installed)."""
        rd = self._rescue_dir(p)
        if not rd.is_dir(): return
        installed = self._installed(p)
        for exe in rd.glob("*.exe"):
            if exe.name.split("-", 1)[0].lower() in installed:
                try: exe.unlink()
                except OSError: pass

    def _center_attempt_pending(self, p: Prefix) -> bool:
        """An installer still sits in the Center's temp folder: it is downloading or
        about to run it itself. Not the moment to race it with our copy."""
        t = self._temp_dir(p)
        return t.is_dir() and any(_INSTALLER.search(x.name) for x in t.glob("*/*.exe"))

    def _kill_doomed_attempt(self, p: Prefix):
        """The Center's own installer run can only end in a native error dialog
        ("Unknown option: --mode unattended", its mis-quoted arguments) that sits
        on screen for ~10 seconds; kill it as soon as it shows up so the dialog
        barely flashes. The app's own rescue runs always carry --unattendedmodeui
        and are left alone."""
        doomed = [pid for pid, cmd in p.processes("-installer.exe")
                  if "--unattendedmodeui" not in cmd]
        if doomed: p.kill_pids(doomed, wait=0)

    def watch_program(self, p, prog, proc):
        """While the Center runs: snapshot what it downloads, and finish each install
        from the snapshot as soon as the Center's own attempt failed and was cleaned
        up -- a Refresh in the still-open Center then shows the product installed."""
        if "software center" not in prog.name.lower(): return
        while proc.poll() is None:
            try:
                self._rescue_snapshot(p)
                self._kill_doomed_attempt(p)
                if not self._center_attempt_pending(p):
                    self._rescue_clean(p)
                    if self.staged_installs(p): self._finish_and_tell(p)
            except Exception:
                pass
            time.sleep(2)
        try:        # the Center is gone: complete whatever is left, racing no one
            self._rescue_snapshot(p); self._rescue_clean(p)
            if self.staged_installs(p): self._finish_and_tell(p)
        except Exception:
            pass

    def _finish_and_tell(self, p: Prefix):
        """Finish rescued installs and say so on the desktop: the Center has just
        shown the user its own install failing, and nothing else tells them the
        install still happened."""
        from ... import host
        for d in self.finish_installs(p):
            if d["ok"]:
                host.notify(f"{_pretty(d['product'])} installed",
                            "The Software Center's own install fails under Wine, so the "
                            "error it shows is expected; vstenv finished the install from "
                            "the downloaded installer. Press Refresh in the Center to see it.")

    def finish_installs(self, p: Prefix, r=None) -> list[dict]:
        """Run each staged installer in InstallBuilder's unattended mode, the
        arguments split properly (see the module docstring)."""
        from ...progress import null_reporter
        rr = null_reporter(r); done = []
        for name, exe in self.staged_installs(p):
            rr.step(f"Finishing Audio Modeling install: {name}")
            try:
                win = "C:\\" + str(exe.relative_to(p.drive_c)).replace("/", "\\")
                cp = p.run([win, "--mode", "unattended", "--unattendedmodeui", "none"], timeout=900)
                (rr.ok if cp.returncode == 0 else rr.fail)(f"exit {cp.returncode}")
                done.append({"vendor": self.id, "product": name, "ok": cp.returncode == 0})
            except Exception as e:
                rr.fail(str(e)[:100]); done.append({"vendor": self.id, "product": name, "ok": False})
        self._rescue_clean(p)
        return done

    def after_install(self, p, r=None):
        """Unattended installs are silent, so finishing them automatically after any
        change is safe (unlike vendors whose installers open windows). The Center
        itself may have just arrived: its policy key must exist before first launch."""
        self.prepare(p, r)
        self._rescue_clean(p)
        if self.staged_installs(p): self.finish_installs(p, r)

    def product_notes(self, p):
        return [Note("Software Center", "patched",
                     "A WebView2 (embedded Edge) app: it needs the WebView2 runtime the app installs, the patched ole32.dll "
                     "and DXVK's dummy composition swapchain to draw and take typing; flicker-free full-frame presentation "
                     "comes from WebView2 policy flags the app sets. Its product installs fail under Wine (it mis-quotes "
                     "the installer's arguments), so the "
                     "app rescues each download and finishes the install itself -- expect the Center to report a failed "
                     "install, then show the product installed on the next Refresh."),
                Note("SWAM", "patched",
                     "JUCE 8 GUIs: they draw through DirectComposition, so they run on the app's patched dcomp.dll and "
                     "dxgi.dll with Wine's own Direct3D, applied automatically. Two first-run notes: if an instrument says "
                     "unlicensed, click Authorize All in the Software Center (its own install flow would have done that, "
                     "but it fails under Wine and this app finishes only the install); and SWAM instruments are silent "
                     "without an expression signal by design -- raise the Expression slider or send CC11.")]

VENDOR = AudioModeling()
