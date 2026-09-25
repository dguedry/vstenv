"""Command-line front end. Every subcommand is a thin call into the package."""
import argparse, sys
from pathlib import Path
from . import __version__, APP_NAME, paths, wine, yabridge, doctor, prefixes, programs, setup, vendors, menu
from .progress import ConsoleReporter

def _prefix(create=False) -> wine.Prefix:
    b = wine.installed_build()
    if b is None:
        if not create: sys.exit(f"wine not provisioned yet — run: {APP_NAME} setup")
        b = wine.provision(ConsoleReporter())
    return wine.Prefix(paths.PREFIX, b)

def _fail_if(r, what="finished with failures"):
    if r.failed: sys.exit(f"{what}: {[s.name for s in r.failed]}")

def cmd_setup(a):
    r = ConsoleReporter(); b = wine.provision(r); p = wine.Prefix(paths.PREFIX, b)
    setup.setup(p, r, Path(a.installer) if a.installer else None)
    _fail_if(r, "setup finished with failures")
    print()
    for v in vendors.with_manager():
        if v.manager_installed(p):
            n = v.manager_notice(p)
            print(f"{v.manager_name}: installed{' — ' + n if n else ''}. Start it with: {APP_NAME} manager {v.id} launch")
        else:
            print(f"{v.manager_name} is not installed. Get it from {v.download_page} ({v.installer_hint}), then: {APP_NAME} manager {v.id} install <file>")

def cmd_vendors(a):
    p = _prefix() if wine.installed_build() else None
    for v in vendors.all():
        line = f"  {v.id:<6} {v.name}"
        if v.manager_name:
            st = "not installed"
            if p is not None and p.exists and v.manager_installed(p): st = f"installed {v.manager_version(p) or ''}".strip()
            line += f"  — {v.manager_name}: {st}"
        print(line)

def cmd_manager(a):
    v = vendors.get(a.vendor); r = ConsoleReporter()
    if not v.manager_name: sys.exit(f"{v.name} has no manager application")
    if a.action == "install":
        if not a.file: sys.exit(f"usage: {APP_NAME} manager {v.id} install <installer>")
        p = _prefix(create=True); v.install_manager(p, Path(a.file), r); _fail_if(r)
        setup.after_change(p, r); print(f"\n{v.manager_name} installed. Start it with: {APP_NAME} manager {v.id} launch")
    elif a.action == "launch":
        p = _prefix(); proc = v.launch_manager(p, r, args=a.args)
        print(f"{v.manager_name} started (pid {proc.pid})")
        if a.wait:
            proc.wait(); print(f"{v.manager_name} exited; finishing installs and bridging")
            v.finish_installs(p, r); setup.after_change(p, r)
    elif a.action == "repair":
        p = _prefix(); v.repair_manager(p, r); _fail_if(r)
    else:
        p = _prefix()
        print(f"{v.manager_name}: {'installed ' + (v.manager_version(p) or '') if v.manager_installed(p) else 'not installed'}")
        n = v.manager_notice(p)
        if n: print(n)
        for k, val in v.status(p).items(): print(f"  {k:<22} {val}")

def cmd_products(a):
    p = _prefix()
    for v in vendors.all():
        if a.vendor and v.id != a.vendor: continue
        rows = v.products(p)
        if not rows: continue
        print(f"{v.name}:")
        w = max(len(x.name) for x in rows)
        for x in rows:
            flags = [x.kind or "?", "registered" if x.registered else "NOT REGISTERED"]
            if x.licensed is not None: flags.append("licensed" if x.licensed else "no license")
            print(f"  {x.name.ljust(w)}  {x.version:8}  {', '.join(flags)}")

def cmd_install(a):
    p = _prefix(); r = ConsoleReporter(); src = Path(a.installer)
    v = vendors.for_product_installer(src)
    if v is not None and not a.generic:
        res = v.install_product(p, src, r, keep_trace=a.keep_trace)
        print(f"  {res['name']}: installed via {res['method']}")
    else:
        programs.install(p, src, r)
    if not a.no_sync: setup.after_change(p, r)

def cmd_sync(a):
    p = _prefix(); r = ConsoleReporter()
    res = yabridge.sync(p, r, extras=a.dirs); menu.sync(p, r)
    print(res["summary"])

def cmd_programs(a):
    p = _prefix(); rows = programs.installed(p)
    if not rows: print("no programs found in the prefix"); return
    w = max(len(x.name) for x in rows)
    for x in rows:
        print(f"  {x.name.ljust(w)}  {x.version:10}  {x.exe or '(uninstall only)'}")

def cmd_run(a):
    p = _prefix(); r = ConsoleReporter()
    prog = programs.find(p, a.name); proc = programs.run(p, prog, r)
    if a.wait:
        proc.wait(); setup.after_change(p, r)           # the program may have installed plugins

def cmd_uninstall(a):
    p = _prefix(); r = ConsoleReporter()
    programs.uninstall(p, programs.find(p, a.name), r)
    if not a.no_sync: setup.after_change(p, r)

def cmd_menu(a):
    p = _prefix(); r = ConsoleReporter()
    if a.action == "remove": menu.remove_all(r)
    else: menu.sync(p, r)

def cmd_doctor(a):
    p = _prefix() if wine.installed_build() else None
    bad = 0
    for c in doctor.run(p):
        mark = "✓" if c.ok else "✗"; bad += not c.ok
        line = f"  {mark} {c.name}" + (f"  ({c.detail})" if c.detail else "")
        if not c.ok and c.fix: line += f"\n      fix: {c.fix}"
        print(line)
    sys.exit(1 if bad else 0)

def cmd_status(a):
    p = _prefix(); print(yabridge.status(p))

def cmd_dxvk(a):
    from . import dxvk
    p = _prefix()
    if a.action == "status":
        st = dxvk.status(p)
        print(f"vulkan: {'yes' if st['vulkan_ok'] else 'no'} — {st['vulkan']}")
        print(f"dxvk:   {st['version'] + ' installed' if st['installed'] else 'not installed'} (wanted {st['wanted']})")
        return
    r = ConsoleReporter()
    if a.action == "remove": dxvk.uninstall(p, r)
    else: dxvk.install(p, r, force=a.force)

def cmd_prefixes(a):
    p = _prefix()
    ports = tuple(x for v in vendors.all() for x in v.daemon_ports)
    print(prefixes.report(p, ports, yabridge.status(p)))

def cmd_url_handlers(a):
    from . import urlschemes
    p = _prefix(); r = ConsoleReporter()
    pairs = urlschemes.all_schemes(p)
    if not pairs: print("no vendor module declares a URL scheme"); return
    for v, s in pairs:
        if a.action == "status":
            st = urlschemes.status(s)
            print(f"{s.scheme}:// ({v.name}): {'ok' if st['ok'] else ('handled by ' + st['default'] if st['foreign'] else 'not registered')}")
        elif a.action == "unregister": urlschemes.unregister(s, r)
        else: urlschemes.register(p, s, r)

def cmd_report(a):
    from . import report
    b = wine.installed_build(); p = wine.Prefix(paths.PREFIX, b) if b else None
    if a.summary_only: print(report.summary(p)); return
    dest = report.write_bundle(Path(a.output) if a.output else None, p)
    print(f"wrote {dest}\nIt contains a summary and this app's logs, with serials, licence tokens and your home directory removed.")

def cmd_rescue(a):
    p = _prefix(); r = ConsoleReporter(); res = []
    for v in vendors.all(): res += v.rescue_installs(p, r)
    if not res: print("nothing to finish (no stalled or staged install found)")
    else:
        for x in res: print(f"  {x.get('name')}: {x.get('method')}")
        setup.after_change(p, r)

def cmd_finish_installs(a):
    p = _prefix(); r = ConsoleReporter(); res = []
    if not any(v.staged_installs(p) for v in vendors.all()): print("no interrupted installs found"); return
    for v in vendors.all(): res += v.finish_installs(p, r)
    for x in res: print(f"  {x['name']}: finished via {x['method']}")
    if not a.no_sync: setup.after_change(p, r)
    _fail_if(r)

def main(argv=None):
    ap = argparse.ArgumentParser(prog=APP_NAME, description="A managed environment for Windows audio plugins on Linux: vendor managers, their products, and VST bridging — without touching Wine yourself.")
    ap.add_argument("--version", action="version", version=__version__)
    sp = ap.add_subparsers(dest="cmd", required=True)
    s = sp.add_parser("setup", help="create the prefix, install every prerequisite and vendor fix (idempotent)")
    s.add_argument("--installer", help="a vendor manager's installer (e.g. Native-Access-latest.exe) to install as part of setup"); s.set_defaults(f=cmd_setup)
    sp.add_parser("vendors", help="list the vendor modules and their managers").set_defaults(f=cmd_vendors)
    s = sp.add_parser("manager", help="a vendor's manager application: install <file> | launch | repair | status")
    s.add_argument("vendor"); s.add_argument("action", choices=["install", "launch", "repair", "status"]); s.add_argument("file", nargs="?")
    s.add_argument("--wait", action="store_true", help="launch: wait for it to exit, then finish installs and bridge"); s.add_argument("args", nargs="*"); s.set_defaults(f=cmd_manager)
    s = sp.add_parser("products", help="list what the vendors' managers installed"); s.add_argument("--vendor"); s.set_defaults(f=cmd_products)
    s = sp.add_parser("install", help="install from an installer: a vendor's product setup (driven silently, finished by hand if it fails) or any Windows installer")
    s.add_argument("installer"); s.add_argument("--generic", action="store_true", help="run it interactively even if a vendor module recognises it")
    s.add_argument("--keep-trace", action="store_true"); s.add_argument("--no-sync", action="store_true"); s.set_defaults(f=cmd_install)
    s = sp.add_parser("sync", help="bridge the prefix's plugins to Linux DAWs with yabridge and refresh the menu"); s.add_argument("dirs", nargs="*", help="extra plugin directories"); s.set_defaults(f=cmd_sync)
    sp.add_parser("programs", help="list Windows programs installed in the prefix").set_defaults(f=cmd_programs)
    s = sp.add_parser("run", help="start an installed program (name or part of it)"); s.add_argument("name"); s.add_argument("--wait", action="store_true"); s.set_defaults(f=cmd_run)
    s = sp.add_parser("uninstall", help="run an installed program's own uninstaller"); s.add_argument("name"); s.add_argument("--no-sync", action="store_true"); s.set_defaults(f=cmd_uninstall)
    s = sp.add_parser("menu", help="desktop menu entries for the prefix's programs"); s.add_argument("action", nargs="?", default="update", choices=["update", "remove"]); s.set_defaults(f=cmd_menu)
    sp.add_parser("doctor", help="check every fix and prerequisite").set_defaults(f=cmd_doctor)
    sp.add_parser("status", help="yabridge status").set_defaults(f=cmd_status)
    s = sp.add_parser("dxvk", help="Direct3D on Vulkan for plugin GUIs that Wine draws wrong")
    s.add_argument("action", nargs="?", default="install", choices=["install", "remove", "status"]); s.add_argument("--force", action="store_true"); s.set_defaults(f=cmd_dxvk)
    sp.add_parser("prefixes", help="which vstenv prefixes exist, who owns a vendor daemon, what yabridge points at").set_defaults(f=cmd_prefixes)
    s = sp.add_parser("url-handlers", help="desktop handlers for the vendors' sign-in callback links"); s.add_argument("action", nargs="?", default="register", choices=["register", "unregister", "status"]); s.set_defaults(f=cmd_url_handlers)
    s = sp.add_parser("report", help="write a diagnostic bundle to send with a bug report"); s.add_argument("-o", "--output"); s.add_argument("--summary-only", action="store_true"); s.set_defaults(f=cmd_report)
    sp.add_parser("rescue-install", help="an installer has stopped responding: stop it and finish the install from its payload").set_defaults(f=cmd_rescue)
    s = sp.add_parser("finish-installs", help="complete installs a vendor's manager started but did not finish"); s.add_argument("--no-sync", action="store_true"); s.set_defaults(f=cmd_finish_installs)
    a = ap.parse_args(argv); a.f(a)

if __name__ == "__main__": main()
