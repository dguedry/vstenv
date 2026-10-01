"""Audio Modeling: the Software Center and the SWAM instruments it installs.

The Software Center is a WebView2 app; it runs (and its sign-in types) thanks to
the WebView2 runtime, the patched ole32.dll and DXVK's dummy composition
swapchain, so it needs no launch wrapper and keeps its desktop menu entry. What
does fail under Wine is the last step of its product installs: it downloads a
SWAM instrument and runs its BitRock InstallBuilder installer, whose GUI mode
misbehaves here (an "unknown mode" error, screen flicker, the pointer vanishing)
and aborts. The downloaded installer is left staged under the Center's temp
directory, and the same installer completes fine in its documented unattended
mode. This module finds those staged installers and finishes them silently:
after anything changes (so closing the Center completes what it started), from
"Finish interrupted installs", and from `vstenv finish-installs`.
"""
from __future__ import annotations

import re
from pathlib import Path

from .. import Vendor, Product, Note
from ...wine import Prefix

_INSTALLER = re.compile(r"-windows(-x64)?-installer\.exe$", re.I)

class AudioModeling(Vendor):
    id = "am"
    name = "Audio Modeling"
    publisher = re.compile(r"audio\s*modeling", re.I)
    # No manager_name on purpose: the Software Center needs no launch fixes, and a
    # vendor manager would lose its desktop menu entry (vendors.run_through_app).

    def _products_dir(self, p: Prefix) -> Path:
        return p.drive_c / "Program Files/Audio Modeling"

    def _temp_dir(self, p: Prefix) -> Path:
        return p.user_dir / "AppData/Roaming/Audio Modeling/Software Center/temp"

    def products(self, p):
        out = []
        d = self._products_dir(p)
        if not d.is_dir(): return out
        for sub in sorted(d.iterdir()):
            if sub.is_dir() and sub.name != "Software Center":
                out.append(Product(name=sub.name, vendor=self.id, kind="Instrument",
                                   install_dir=str(sub)))
        return out

    # -- finishing the Software Center's aborted installs --------------------------------
    def staged_installs(self, p: Prefix) -> list:
        """Installers the Center downloaded whose product is not installed:
        [(name, installer path)]."""
        t = self._temp_dir(p)
        if not t.is_dir(): return []
        installed = {x.name.lower() for x in self._products_dir(p).iterdir() if x.is_dir()} \
            if self._products_dir(p).is_dir() else set()
        out = []
        for sub in sorted(t.iterdir()):
            if not sub.is_dir(): continue
            for exe in sorted(sub.glob("*.exe")):
                if not _INSTALLER.search(exe.name): continue
                # "SWAMViolin-3.12.3-2944-windows-x64-installer.exe" -> "swamviolin"
                product = exe.name.split("-", 1)[0].lower()
                if any(product == n.replace(" ", "").lower() for n in installed): continue
                out.append((exe.name.split("-", 1)[0], exe))
        return out

    def finish_installs(self, p: Prefix, r=None) -> list[dict]:
        """Run each staged installer in InstallBuilder's unattended mode (its GUI
        mode aborts under Wine with an unknown-mode error and screen flicker)."""
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
        return done

    def after_install(self, p, r=None):
        """Unattended installs are silent, so finishing them automatically after any
        change is safe (unlike vendors whose installers open windows)."""
        if self.staged_installs(p): self.finish_installs(p, r)

    def launch_env(self, p, prog):
        if "software center" not in prog.name.lower(): return {}
        # The Center's page renders fine GPU-composited (DXVK's dummy composition
        # swapchain), but embedded video (the product TRY pages play a YouTube
        # clip) takes Chromium's overlay path and flickers constantly. Software
        # compositing renders the whole page, video included, through one path.
        return {"WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS": "--disable-gpu"}

    def product_notes(self, p):
        return [Note("Software Center", "patched",
                     "A WebView2 (embedded Edge) app: it needs the WebView2 runtime the app installs, the patched ole32.dll "
                     "and DXVK's dummy composition swapchain to draw and take typing. Its product installs abort under Wine "
                     "in the installer's GUI mode; the app finishes them silently from the staged download."),
                Note("SWAM", "patched",
                     "JUCE 8 GUIs: they draw through DirectComposition, so they run on the app's patched dcomp.dll and "
                     "dxgi.dll with Wine's own Direct3D, applied automatically.")]

VENDOR = AudioModeling()
