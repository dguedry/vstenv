"""Steinberg: the Download Assistant and what it installs.

The Steinberg Download Assistant (SDA) is a JavaFX application with its own
Java runtime. Two things break under Wine (found with SDA 1.40.1, 2026-09-25):

- Text renders as blobs, then as heavy jagged glyphs. Two layers:
  (1) JavaFX rasterises some glyphs through Direct2D; Wine's Direct2D draws
  through D3D11, and with DXVK serving D3D11 every such glyph fails
  (DWGlyph.createRenderingTarget NullPointerException, 300 per screen).
  A per-application DllOverrides key giving this one exe Wine's built-in
  D3D11/DXGI/D3D10/D3D9 fixes that; DXVK stays in place for the plugins.
  (2) Most glyphs go through JavaFX's ClearType path: it asks DirectWrite for
  the three-subpixel texture and composites it as LCD text. Wine fills that
  texture by copying one grey coverage value into all three subpixels, and
  JavaFX's LCD blending turns it into bold, aliased text. `-Dprism.lcdtext=false`
  makes JavaFX use grey masks and the text is clean. It must go into the
  launcher's [JVMOptions]: [JVMUserOptions] is only read into the user's Java
  preferences on the first run and ignored afterwards.
- Sign-in never comes back (SDA and the Activation Manager alike). SDA opens the Steinberg ID login in the system
  browser; the flow page ends by opening a net-steinberg-sda:// link, which
  starts a second SDA instance that hands the URL to the running one over its
  IPC. The host browser needs a handler for that scheme (urlschemes.py); the
  page's "Try Again" then completes the login.

Steinberg's current products (HALion Sonic 7 and, by the same library,
Cubase and Dorico of that generation) draw through DirectComposition in
their GUI library graphics2d.dll. Stock Wine implements it as stubs (11.17,
staging and Proton alike: a device is created, the first CreateSurface fails,
and the program aborts with c0000409; a dcomp.dll that refuses to create a
device only turns that into Steinberg's "serious graphic driver related issue"
dialog, so there is no fallback path). vstenv therefore ships a patched
dcomp.dll (winefixes.py: wine-staging's DirectComposition plus the pieces
Steinberg hits, built by CI from patches/wine/) and installs it into its Wine
build; with it in place HALion Sonic 7 renders fully (verified 2026-09-26).
Every Steinberg program also gets the per-application built-in D3D override
(after_install / repair): Direct2D on DXVK draws blobs, and DXVK's swap chain
for composition is not implemented. Until the patched DLL is installed,
programs whose graphics2d imports dcomp.dll are refused at launch with that
reason (cannot_run) instead of crashing; Health lists them. Their VST3 GUIs
inside a DAW's yabridge host use the same Wine build, so they follow suit.

The Activation Manager's sign-in is kept by the License Engine as a Windows
credential with an attribute; stock Wine drops credential attributes, so the
engine found an "old format" token at every restart, reset it ("You were
signed out automatically"), and then popped the Activation Manager up whenever
a product asked for a license. The patched advapi32.dll that ships with the
app (winefixes) keeps attributes. Another Wine-only nuisance: the Activation
Manager ignores clicks for the seconds during which the engine is busy with a
product that just started (HALion connecting), so a click on "Sign In" right
then does nothing; a second click a moment later works.

Steinberg's dialogs (quit confirmation, crash reporter, notices) draw their
text with DirectWrite in "Segoe UI". GDI substitutes do not reach DirectWrite,
so without a family of that name the dialogs came up with blank buttons and no
message; fontalias installs Selawik under that name (runtime.install).

SDA installs its runtime components (Activation Manager, Library Manager,
built-in ASIO driver, MediaBay) and every product through the Steinberg
Install Assistant and its Install Helper, which are .NET executables: with
mscoree disabled they die with STATUS_DLL_NOT_FOUND and SDA reports "failed
to update Steinberg Library Manager". Under Wine Mono (mono.py) they run and
the components install; MediaBay's installer still exits 231. Their installer
packages are RAR self-extractors (7-Zip 24+ opens them) holding a .NET
SetupBootstrapper and an MSI; the MSI installs fine with msiexec directly.
"""
from __future__ import annotations

import re
from pathlib import Path

from .. import Vendor, Product, Check, UrlScheme, Note
from ...progress import null_reporter
from ... import mono, pe
from ...installers import steinberg as pkg
from ...wine import Prefix

MANAGER = "Steinberg Download Assistant"
EXE = "Steinberg Download Assistant.exe"
DEFAULT_EXE = r"C:\Program Files (x86)\Steinberg\Download Assistant\Steinberg Download Assistant.exe"
OVERRIDES = ("d3d9", "d3d10core", "d3d11", "dxgi")
OVERRIDES_KEY = rf"HKCU\Software\Wine\AppDefaults\{EXE}\DllOverrides"
PRODUCT_OVERRIDES = ("d3d10core", "d3d11", "dxgi")                 # VSTGUI/Direct2D: built-in D3D11 for Steinberg programs

def overrides_key(exe_name: str) -> str: return rf"HKCU\Software\Wine\AppDefaults\{exe_name}\DllOverrides"

def program_fix_applied(p: Prefix, exe_name: str) -> bool:
    cur = p.reg_query(overrides_key(exe_name))
    return all(cur.get(d) == "builtin" for d in PRODUCT_OVERRIDES)

def apply_program_fixes(p: Prefix, exe_names: list[str], reporter=None) -> list[str]:
    """Built-in D3D11 for each Steinberg program exe (VSTGUI draws through Direct2D). Returns what changed."""
    r = null_reporter(reporter); changed = []
    for name in exe_names:
        if program_fix_applied(p, name): continue
        for d in PRODUCT_OVERRIDES: p.reg_add(overrides_key(name), d, "builtin")
        changed.append(name)
    if changed:
        r.step("Steinberg programs: built-in Direct3D 11 (their GUIs draw through Direct2D)"); r.ok(", ".join(changed))
    return changed
SCHEME = "net-steinberg-sda"
# The Activation Manager (Qt) signs in the same way: system browser, then the flow
# page opens net-steinberg-activation-manager://…, which the prefix registers as
# `SteinbergActivationManager.exe --redirect "%1"`.
SAM_SCHEME = "net-steinberg-activation-manager"
RUNTIME_COMPONENTS = ("activation manager", "library manager", "mediabay", "install assistant", "asio driver", "download assistant")
SAM_EXE = r"C:\Program Files\Steinberg\Activation Manager\SteinbergActivationManager.exe"

JVM_OPTIONS = ("-Dprism.lcdtext=false",)     # grey text masks: see the module docstring
CFG_MARK = "# vstenv: grey text masks (Wine fills ClearType textures with grey; JavaFX's LCD blending makes them bold and jagged)"

def launcher_cfg(p: Prefix, exe: str | None = None) -> Path:
    """javapackager's `<app dir>/app/<App>.cfg` next to the exe."""
    e = p.to_host(exe or DEFAULT_EXE)
    return e.parent / "app" / (e.stem + ".cfg")

def cfg_fixed(cfg: Path) -> bool:
    try: text = cfg.read_text(encoding="utf-8", errors="replace")
    except OSError: return False
    sect = text.split("[JVMOptions]", 1)[1].split("\n[", 1)[0] if "[JVMOptions]" in text else ""
    return all(o in sect for o in JVM_OPTIONS)

def fix_cfg(cfg: Path) -> bool:
    """Put JVM_OPTIONS into the launcher's [JVMOptions]. Returns True if changed.
    The Download Assistant replaces this file when it updates itself, so this
    runs again before every launch."""
    if cfg_fixed(cfg): return False
    text = cfg.read_text(encoding="utf-8", errors="replace")
    if "[JVMOptions]" not in text: raise LookupError(f"{cfg.name} has no [JVMOptions] section")
    head, rest = text.split("[JVMOptions]\n", 1)
    sect, _, tail = rest.partition("\n[")
    lines = [l for l in sect.splitlines() if l not in JVM_OPTIONS and l != CFG_MARK]
    sect = "\n".join([CFG_MARK, *JVM_OPTIONS, *lines]).rstrip("\n") + "\n"
    cfg.write_text(head + "[JVMOptions]\n" + sect + ("\n[" + tail if tail else ""), encoding="utf-8")
    return True

def text_fix_applied(p: Prefix, exe: str | None = None) -> bool:
    cur = p.reg_query(OVERRIDES_KEY)
    return all(cur.get(d) == "builtin" for d in OVERRIDES) and cfg_fixed(launcher_cfg(p, exe))

def apply_text_fix(p: Prefix, reporter=None, exe: str | None = None) -> bool:
    """Returns True if something was changed."""
    r = null_reporter(reporter); changed = []
    r.step(f"{MANAGER}: text rendering (built-in Direct3D for this app, grey text masks)")
    cur = p.reg_query(OVERRIDES_KEY)
    missing = [d for d in OVERRIDES if cur.get(d) != "builtin"]
    for d in missing: p.reg_add(OVERRIDES_KEY, d, "builtin")
    if missing: changed.append(", ".join(missing) + " = builtin")
    cfg = launcher_cfg(p, exe)
    if cfg.exists():
        try:
            if fix_cfg(cfg): changed.append(" ".join(JVM_OPTIONS))
        except (OSError, LookupError) as e: r.fail(str(e)[:100]); return bool(changed)
    if not changed: r.skip("already"); return False
    r.ok("; ".join(changed)); return True

class Steinberg(Vendor):
    id = "steinberg"
    name = "Steinberg"
    publisher = re.compile(r"steinberg", re.I)
    manager_name = MANAGER
    download_page = "https://o.steinberg.net/en/support/downloads/steinberg_download_assistant.html"
    installer_hint = "the Steinberg Download Assistant installer for Windows (.exe)"

    def _manager(self, p: Prefix):
        from ... import programs
        return next((x for x in programs.installed(p) if "download assistant" in x.name.lower()), None)
    def _exe(self, p: Prefix) -> str:
        m = self._manager(p)
        return m.exe if m and m.exe else DEFAULT_EXE

    # -- Download Assistant --------------------------------------------------------------
    def manager_installed(self, p): return self._manager(p) is not None
    def manager_version(self, p):
        m = self._manager(p); return (m.version or None) if m else None
    def manager_exe_names(self): return (EXE,)
    def accepts_manager_installer(self, f):
        return bool(re.search(r"steinberg[-_ ]?download[-_ ]?assistant", Path(f).name, re.I))
    def install_manager(self, p, installer, r=None):
        from ... import programs
        programs.install(p, installer, r)          # its own window
        self.fixes(p, r)
    def repair_manager(self, p, r=None): self.fixes(p, r)
    def fixes(self, p, r=None):
        apply_text_fix(p, r, self._exe(p))
        apply_program_fixes(p, self.program_exes(p), r)
        mono.install(p, r)                         # the Install Assistant it drives is .NET
    def launch_manager(self, p, r=None, args=()):
        from ... import programs
        m = self._manager(p)
        if m is None: raise RuntimeError(f"{MANAGER} is not installed yet — get it from {self.download_page} and install it from the Install tab")
        apply_text_fix(p, r, m.exe or None)      # SDA's self-update rewrites the launcher config
        return programs.run(p, m, r)
    def is_manager_program(self, prog): return "download assistant" in prog.name.lower()

    # -- content ------------------------------------------------------------------------------
    def products(self, p):
        """Steinberg products register ordinary Uninstall entries; that is the inventory."""
        from ... import programs
        return [Product(name=x.name, vendor=self.id, kind="App", version=x.version, install_dir=x.install_dir)
                for x in programs.installed(p) if self.publisher.search(x.publisher or "") and not self.is_manager_program(x)]
    def url_schemes(self, p):
        return [UrlScheme(SCHEME, f"{MANAGER} login callback", lambda p: [str(p.build.wine), str(p.to_host(self._exe(p)))]),
                UrlScheme(SAM_SCHEME, "Steinberg Activation Manager login callback",
                          lambda p: [str(p.build.wine), str(p.to_host(SAM_EXE)), "--redirect"])]
    def needs_dcomp(self, p, install_dir: str) -> list[str]:
        """Files of a program that import DirectComposition (dcomp.dll)."""
        try: d = p.to_host(install_dir)
        except Exception: return []
        if not d.is_dir(): return []
        return sorted(f.name for f in d.iterdir() if f.suffix.lower() in (".dll", ".exe") and "dcomp.dll" in pe.imports(f))
    def is_runtime_component(self, prog) -> bool:
        """SDA's own tooling: Activation Manager, Library Manager, MediaBay server,
        Install Assistant, built-in ASIO driver. Several import dcomp.dll (Qt does,
        conditionally) but run fine; the limit is Steinberg's products' GUIs."""
        n = prog.name.lower()
        return self.is_manager_program(prog) or any(k in n for k in RUNTIME_COMPONENTS)
    def dcomp_ready(self, p) -> bool:
        """The patched dcomp.dll is in the Wine build (winefixes)."""
        from ... import winefixes
        return winefixes.has(getattr(p, "build", None), "dcomp.dll")
    def cannot_run(self, p, prog):
        if self.is_runtime_component(prog) or not prog.install_dir: return None
        files = [f for f in self.needs_dcomp(p, prog.install_dir) if f.lower() == "graphics2d.dll"]
        if not files or self.dcomp_ready(p): return None
        return (f"it draws through DirectComposition ({', '.join(files[:3])} import dcomp.dll), which stock Wine does not implement; "
                "it would abort at start, and its plugin GUI would too. Run setup: it installs this app's patched dcomp.dll "
                "into its Wine (vstenv wine-fixes install), after which these programs run.")
    def program_exes(self, p) -> list[str]:
        """Exe names of Steinberg-published programs other than the Download Assistant."""
        from ... import programs
        return sorted({x.exe.rsplit("\\", 1)[-1] for x in programs.installed(p)
                       if x.exe and self.publisher.search(x.publisher or "") and not self.is_manager_program(x)})
    def after_install(self, p, r=None): apply_program_fixes(p, self.program_exes(p), r)
    def product_notes(self, p):
        dcomp = self.dcomp_ready(p)
        gui = ("Steinberg rewrote its GUI library on DirectComposition, Direct2D and DirectWrite together, the one Windows graphics stack nobody "
               "outside Redmond implements, and made it abort rather than fall back when any piece is missing: an instrument that cannot draw a "
               "button without a desktop compositor. It runs here only because this app ships a patched dcomp.dll, forces Wine's own Direct3D "
               "for it and its bridged plugin, and installs a Segoe UI font family so its dialogs are not blank.") if dcomp else \
              ("Steinberg rewrote its GUI library on DirectComposition, which Wine does not have, and made it abort rather than fall back. "
               "This app's patched dcomp.dll is not installed, so it is refused at launch; run setup.")
        return [Note(MANAGER, "patched", "Steinberg's installer chain is a museum of Microsoft technology: a Java 8 / JavaFX downloader starts a "
                     ".NET Install Assistant that runs PowerShell scripts signed for a Windows trust check, and when that check fails it refuses to "
                     "install Steinberg's own packages. Five runtimes to copy a file. This app fixes its unreadable text (Wine's own Direct3D "
                     "and a JavaFX option), registers its browser sign-in link, installs Wine Mono for the .NET part, and unpacks the refused "
                     "packages to run their MSIs directly."),
                Note("Activation Manager", "patched", "A Qt 6 front end to a licence engine that talks nanomsg over named pipes to store one token, and "
                     "stores it as a Windows credential with an attribute Wine used to throw away: so it forgot your sign-in on every restart, then "
                     "opened itself in your face each time a plugin asked for a licence. This app's patched advapi32.dll keeps the attribute. "
                     "It still ignores clicks for a few seconds whenever a product connects to the engine."),
                Note("HALion", "patched" if dcomp else "cannot", gui), Note("Cubase", "patched" if dcomp else "cannot", gui), Note("Dorico", "patched" if dcomp else "cannot", gui),
                Note("", "works", "Runs as is with the per-program Direct3D override.")]
    def plugin_dll_overrides(self, p):
        """Steinberg's plugins draw like its programs (Direct2D, DirectComposition):
        on DXVK the host's d3d11 worker thread crashes once the GUI redraws fast
        (playing), so their yabridge host gets Wine's own Direct3D too. The VST3
        bundles live under a "Steinberg" directory (Common Files/VST3/Steinberg,
        Program Files/Steinberg/<product>/VST3)."""
        return [("/Steinberg/", ",".join(PRODUCT_OVERRIDES) + "=b")]
    def logs(self, p):
        d = p.user_dir / "AppData/Local/Steinberg Download Assistant/logs"
        latest = max(d.glob("*.log"), key=lambda f: f.stat().st_mtime, default=None) if d.is_dir() else None
        return {"steinberg-download-assistant.log": latest} if latest else {}

    # -- installs ------------------------------------------------------------------------------
    # The Download Assistant hands every package to the .NET Install Assistant, which
    # refuses any with a signed prerun script under Wine (no PowerShell signature
    # provider). Those are finished here from the package it leaves in Temp.
    def accepts_product_installer(self, f): return pkg.is_package(f)
    def install_product(self, p, installer, r=None, **kw): return pkg.install(p, Path(installer), r)
    def staged_installs(self, p): return pkg.staged(p)
    def finish_installs(self, p, r=None): return pkg.finish_staged(p, r)
    def rescue_installs(self, p, r=None): return pkg.finish_staged(p, r)

    # -- health ------------------------------------------------------------------------------------
    def checks(self, p) -> list[Check]:
        c = []
        m = self._manager(p)
        c.append(Check(f"{MANAGER} installed", m is not None, (m.version if m else "") or "",
                       fix=f"get it from {self.download_page}, then: vstenv manager steinberg install <file>"))
        if m is not None:
            ok = text_fix_applied(p, m.exe or None)
            c.append(Check(f"{MANAGER} text renders (built-in Direct3D, grey masks)", ok, "" if ok else "its text draws as blobs or bold jagged glyphs",
                           fix="vstenv manager steinberg repair"))
            has = mono.installed(p)
            c.append(Check("Wine Mono (.NET) for the Steinberg Install Assistant", has, "" if has else "runtime components and products fail to install without it",
                           fix="vstenv manager steinberg repair"))
            from ... import programs
            blocked = [x.name for x in programs.installed(p) if self.publisher.search(x.publisher or "") and self.cannot_run(p, x)]
            c.append(Check("Steinberg programs that Wine can run", not blocked,
                           "" if not blocked else ", ".join(blocked) + ": need DirectComposition; the patched dcomp.dll is not in this app's Wine (refused at launch with the reason)",
                           fix="vstenv wine-fixes install"))
            missing = [e for e in self.program_exes(p) if not program_fix_applied(p, e)]
            c.append(Check("Steinberg programs run (built-in Direct3D 11)", not missing, ", ".join(missing), fix="vstenv manager steinberg repair"))
            left = pkg.staged(p)
            c.append(Check("no Steinberg installs left unfinished", not left, ", ".join(x.name for x in left), fix="vstenv finish-installs"))
        return c
    def status(self, p) -> dict:
        m = self._manager(p)
        return {"installed": m is not None, "version": m.version if m else None, "text_fix": text_fix_applied(p, m.exe or None) if m else False, "mono": mono.installed(p)}

VENDOR = Steinberg()
