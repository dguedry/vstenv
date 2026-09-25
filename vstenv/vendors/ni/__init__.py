"""Native Instruments: Native Access, the products it installs, and the NTK daemon.

Native Access is an Electron app whose NSIS installer does not run under Wine;
the app files are extracted from it instead, then patched (stack reserve, the
asar bundle) and accompanied by the NTK Daemon service. Its product installers
are InstallAware setups (see installers.installaware). Sign-in returns from the
browser through native-access:// links. See the README for every fix.
"""
from __future__ import annotations

import re
from pathlib import Path

from .. import Vendor, Product, Check, UrlScheme
from ...installers import installaware
from ...programs import Program
from ...wine import Prefix
from . import native_access as na, products as prod

class NativeInstruments(Vendor):
    id = "ni"
    name = "Native Instruments"
    publisher = re.compile(r"native\s*instruments", re.I)
    manager_name = "Native Access"
    download_page = na.NA_DOWNLOAD_PAGE
    installer_hint = "Native-Access-latest.exe (the Windows version)"
    daemon_ports = na.NTK_PORTS

    # -- Native Access -----------------------------------------------------------
    def manager_installed(self, p): return na.na_exe(p).exists()
    def manager_version(self, p):
        v = na.status(p)["version"]
        return ".".join(v.split(".")[:3]) if v else None
    def manager_exe_names(self): return ("Native Access.exe",)
    def manager_notice(self, p):
        v = na.version_notice(p)
        if v["installed_known_bad"]:
            return f"Installed {v['installed']} does not work on this stack: {v['installed_known_bad']}. Install a validated version ({', '.join(sorted(na.KNOWN_GOOD))})."
        if v["newer"]:
            return f"Installed {v['installed']} · {v['latest']} available " + (
                "(validated on this stack)." if v["latest_known_good"] else
                (f"(does not work on this stack: {v['latest_known_bad']}). Stay on {v['installed']}." if v["latest_known_bad"] else "(not yet validated on this stack)."))
        return f"Installed {v['installed']} (current)." if v["installed"] else None
    def accepts_manager_installer(self, f):
        return bool(re.search(r"native[-_ ]?access.*\.exe$", Path(f).name, re.I))
    def install_manager(self, p, installer, r=None): na.install_native_access(p, installer, r)
    def repair_manager(self, p, r=None): na.install_native_access_fixes(p, r)
    def launch_manager(self, p, r=None, args=()): return na.launch(p, r, extra_args=args)

    # -- prefix -------------------------------------------------------------------------
    def prepare(self, p, r=None):
        na.download_location(p, r)

    # -- content ---------------------------------------------------------------------------
    def products(self, p):
        return [Product(name=x.name, vendor=self.id, kind=x.type, version=x.version, install_dir=x.install_dir,
                        content_dir=x.content_dir, registered=x.registered, licensed=x.licensed) for x in prod.installed(p)]
    def programs(self, p):
        out = prod.ni_programs(p)
        if self.manager_installed(p):
            out.append(Program(name=self.manager_name, version=self.manager_version(p) or "", publisher=self.name,
                               exe=p.to_win(na.na_exe(p)), install_dir=p.to_win(na.na_dir(p)), sources=["vendor"]))
        return out
    def plugin_dirs(self): return ["Program Files/Native Instruments/VSTPlugins 64 bit"]
    def content_dirs(self, p): return [v["ContentDir"] for v in prod.hive_keys(p).values() if v.get("ContentDir")]
    def url_schemes(self, p):
        return [UrlScheme("native-access", "Native Access login callback",
                          lambda p: [str(p.build.wine), str(na.na_exe(p))])]
    def logs(self, p): return {"native-access.log": na.na_log(p)}

    # -- installs ------------------------------------------------------------------------------
    def accepts_product_installer(self, f): return installaware.is_setup(f)
    def install_product(self, p, installer, r=None, **kw): return installaware.install(p, installer, r, keep_trace=kw.get("keep_trace", False))
    def watch_installs(self, p, watch=None): return installaware.stalled_setup(p, watch)
    def _busy(self, p, name): return self.manager_running(p)
    def rescue_installs(self, p, r=None): return installaware.rescue(p, r, prod.find_download, self._busy)
    def staged_installs(self, p): return installaware.staged_installs(p, prod.find_download)
    def finish_installs(self, p, r=None): return installaware.finish_staged(p, r, prod.find_download, self._busy)
    def after_install(self, p, r=None): prod.register_all_libraries(p, r)

    # -- health -------------------------------------------------------------------------------------
    def checks(self, p) -> list[Check]:
        from ... import prefixes, urlschemes
        c = []
        loc, ok = na.download_location_status(p)
        c.append(Check("NA download location writable", ok, loc if ok else (f"{loc}: not writable from here" if loc else "unset: every download fails"),
                       fix="vstenv manager ni launch (re-applies) or vstenv setup"))
        s = na.status(p)
        c.append(Check("Native Access installed", s["installed"], s["version"] or "", fix=f"download from {na.NA_DOWNLOAD_PAGE}, then: vstenv manager ni install <file>"))
        if s["installed"]:
            c.append(Check("Native Access stack patch (64MB)", s["stack_patch"], fix="vstenv manager ni launch (re-applies)"))
            c.append(Check("NTK Daemon installed", s["ntk_daemon"], s["ntk_version"] or "", fix="vstenv setup"))
            dep = na.dependency_status(p)
            c.append(Check("NA dependency-check patch", dep != "missing",
                           "built into Native Access 3.26+" if dep == "native" else ("" if dep == "patched" else "NA picks an owned product over the installed Player: libraries show 'Requires Kontakt'"),
                           fix="vstenv setup"))
            c.append(Check("NA self-updater disabled", s["self_update_disabled"], "updates only via a downloaded installer", fix="vstenv setup"))
            v = na.version_notice(p)
            if v["installed_known_bad"]:
                c.append(Check("Native Access version", False, f"{v['installed']} does not work on this stack: {v['installed_known_bad']}",
                               fix="install a validated version (" + ", ".join(sorted(na.KNOWN_GOOD)) + "): vstenv manager ni install <installer>"))
            elif v["newer"]:
                c.append(Check("Native Access version", True, f"{v['installed']} installed; {v['latest']} available "
                               + ("(validated)" if v["latest_known_good"] else ("(does not work on this stack: " + v["latest_known_bad"] + ")" if v["latest_known_bad"] else "(not yet validated on this stack)"))))
            if s["pending_update"]:
                c.append(Check("no pending NA self-update", False, "an update NA downloaded before its updater was disabled; ignore or install it from NI's current installer"))
        # The NTK daemon binds fixed localhost ports, one daemon per machine. A daemon
        # from another prefix holding them means plugins bridged from *this* prefix talk
        # to the wrong daemon and hang in every DAW.
        owner = prefixes.port_owner(na.NTK_PORTS)
        if not owner["busy"]:
            c.append(Check("NTK Daemon ports free or ours", True, "daemon not running (starts with Native Access or the first plugin)"))
        elif owner["prefix"] and Path(owner["prefix"]).resolve() == p.path.resolve():
            c.append(Check("NTK Daemon ports free or ours", True, f"{owner['exe'] or 'daemon'} running in this prefix"))
        elif owner["prefix"]:
            c.append(Check("NTK Daemon ports free or ours", False,
                           f"ports {owner['busy']} held by {owner['exe']} (pid {owner['pid']}) of another prefix: {prefixes.short(owner['prefix'])} -- plugins bridged from this prefix will hang",
                           fix="quit the Native Access / DAW using that prefix"))
        else:
            ours = p.is_running("NTKDaemon.exe") or p.wineserver_running()
            c.append(Check("NTK Daemon ports free or ours", ours,
                           "daemon running (holder not visible from this sandbox)" if ours else f"ports {owner['busy']} held by a process this sandbox cannot see (another prefix's daemon?)",
                           fix="stop the other Native Access / NTKDaemon, then start Native Access from here"))
        for s_ in self.url_schemes(p):
            u = urlschemes.status(s_)
            c.append(Check("browser sign-in returns to Native Access", u["ok"],
                           ("" if u["ok"] else (f"native-access:// links open {u['default']} instead" if u["foreign"] else "nothing handles native-access:// links, so a browser login never comes back")),
                           fix="vstenv url-handlers register"))
        staged = self.staged_installs(p)
        c.append(Check("no interrupted Native Access installs", not staged,
                       "" if not staged else "left half-done: " + ", ".join(f"{x.name}" + (" (download kept)" if x.download else "") for x in staged),
                       fix="vstenv finish-installs"))
        unreg = [x.name for x in prod.installed(p) if x.type == "Content" and not x.registered]
        c.append(Check("libraries registered for Kontakt", not unreg, ", ".join(unreg), fix="vstenv sync"))
        return c

    def status(self, p): return na.status(p)

VENDOR = NativeInstruments()
