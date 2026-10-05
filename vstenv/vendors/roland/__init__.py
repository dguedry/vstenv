"""Roland: the Roland Cloud Manager and the instruments it installs.

The Manager is a Qt 6 application (QtWebEngine inside), delivered by a Qt
Installer Framework setup. Two things about that stack need help under Wine,
and this module carries both:

* Its installer refuses to run. Qt's network-information backend asks WinRT for
  NetworkInformation.GetInternetConnectionProfile, which Wine stubs, and the
  installer answers every command with "Unknown exception caught."
  Overriding netprofm makes CoCreateInstance(NetworkListManager) fail earlier,
  before Qt reaches WinRT, and the setup then runs headless.

* The installed Manager dies at window creation, inside qwindows.dll, with its
  own crash handler swallowing it. Qt looks at the running process to choose a
  platform theme; inside a Flatpak it finds /.flatpak-info, picks
  xdgdesktopportal, gets a bare QPlatformTheme, and QWindowsTheme::instance()
  is NULL. Naming the windows theme explicitly avoids that path.

  The variable cannot simply be exported: Wine does not pass QT_* through to
  the Windows environment. A variable named WINEQT_<name> arrives as QT_<name>,
  which is what launch_env() sets.

Both are properties of Qt 6 under Wine rather than anything Roland does, so
they apply to any Qt Installer Framework setup and any Qt 6 GUI here.

Sign-in happens in the system browser and comes back through a
rolandcloudmanager:// link, so that scheme is registered for the prefix.
"""
from __future__ import annotations

import re
from pathlib import Path

from .. import Vendor, Product, Note, UrlScheme
from ...wine import Prefix

MANAGER = "Roland Cloud Manager"
MANAGER_EXE = "Roland Cloud Manager.exe"
# Roland Cloud Manager's background service, started with the app; it keeps
# running after the window closes and holds the prefix open.
HELPER_EXE = "RCMservice.exe"

# Qt under Wine:
#   netprofm=d      -- a Qt Installer Framework setup answers every command with
#                      "Unknown exception caught." without it (WinRT network
#                      information is a Wine stub).
#   QT_QPA_PLATFORMTHEME=windows
#                   -- a Qt 6 GUI dies at window creation when /.flatpak-info
#                      makes it choose the xdgdesktopportal theme.
# Wine does not pass QT_* into the Windows environment; WINEQT_<name> arrives
# as QT_<name>.
INSTALLER_ENV = {"WINEDLLOVERRIDES": "netprofm=d"}
LAUNCH_ENV = {"WINEQT_QPA_PLATFORMTHEME": "windows"}


class Roland(Vendor):
    id = "roland"
    name = "Roland"
    publisher = re.compile(r"roland", re.I)
    manager_name = MANAGER
    download_page = "https://www.roland.com/global/products/rc_roland_cloud_manager/"
    installer_hint = "the Roland Cloud Manager installer for Windows (.exe)"

    def _manager(self, p: Prefix):
        from ... import programs
        return next((x for x in programs.installed(p)
                     if MANAGER.lower() in x.name.lower()
                     or "rolandcloudmanager" in (x.install_dir or "").lower()), None)

    def _manager_exe(self, p: Prefix) -> Path | None:
        exe = p.drive_c / "Program Files" / "RolandCloudManager" / MANAGER_EXE
        return exe if exe.is_file() else None

    # -- the manager -----------------------------------------------------------------
    def manager_installed(self, p): return self._manager_exe(p) is not None
    def manager_version(self, p):
        m = self._manager(p); return (m.version or None) if m else None
    def manager_exe_names(self): return (MANAGER_EXE, HELPER_EXE)

    def accepts_manager_installer(self, f):
        return bool(re.search(r"roland[-_ ]?cloud[-_ ]?manager", Path(f).name, re.I))

    def install_manager(self, p, installer, r=None):
        """Run the Qt Installer Framework setup without its GUI.

        Its own interface cannot start here (see the module docstring), but IFW
        takes the whole install as command-line arguments, which is what the
        vendor documents for unattended installs."""
        from ...progress import null_reporter
        rep = null_reporter(r)
        rep.step(f"Installing {MANAGER}")
        cp = p.run([str(installer), "install",
                    "--accept-licenses", "--default-answer", "--confirm-command"],
                   env=INSTALLER_ENV, timeout=3600)
        if self._manager_exe(p) is None:
            tail = ((cp.stdout or "") + (cp.stderr or "")).strip().splitlines()[-1:] or [""]
            raise RuntimeError(f"{MANAGER}'s installer left no {MANAGER_EXE} in the prefix: {tail[0][:120]}")
        rep.ok(self.manager_version(p) or "installed")

    def launch_manager(self, p, r=None, args=()):
        from ... import programs
        m = self._manager(p)
        if m is None:
            raise RuntimeError(f"{MANAGER} is not installed yet — get it from {self.download_page} "
                               "and install it from the Install tab")
        return programs.run(p, m, r)

    def is_manager_program(self, prog):
        return MANAGER.lower() in prog.name.lower()

    def launch_env(self, p, prog):
        """Qt's platform theme, for the Manager and anything else Roland ships
        that draws with Qt."""
        return dict(LAUNCH_ENV)

    # -- sign-in ---------------------------------------------------------------------
    def url_schemes(self, p):
        exe = self._manager_exe(p)
        if exe is None: return []
        return [UrlScheme("rolandcloudmanager", f"{MANAGER} login callback",
                          lambda p, exe=exe: [str(p.build.wine), str(exe)])]

    # -- what is installed -----------------------------------------------------------
    def products(self, p):
        """Roland's instruments register ordinary Uninstall entries."""
        from ... import programs
        out = []
        for x in programs.installed(p):
            if self.is_manager_program(x): continue
            if not self.publisher.search(x.publisher or ""): continue
            out.append(Product(name=x.name, vendor=self.id, kind="App",
                               version=x.version, install_dir=x.install_dir))
        return out

    def product_notes(self, p):
        return [Note(MANAGER, "patched",
                     "A Qt 6 application whose installer and window both need help under Wine: its "
                     "Qt Installer Framework setup answers every command with \"Unknown exception "
                     "caught.\" until netprofm is disabled, and the installed app dies at window "
                     "creation unless Qt is told to use the windows platform theme. The app applies "
                     "both; sign-in goes through your browser and returns on a rolandcloudmanager:// link.")]

VENDOR = Roland()
