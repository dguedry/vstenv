"""Command-line front end. Every subcommand is a thin call into the package."""
import argparse, sys, time
from pathlib import Path
from . import __version__, APP_NAME, paths, wine, yabridge, doctor, prefixes, programs, setup, vendors, menu, unmanaged, recipes
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
        p = _prefix(); t0 = time.time(); proc = v.launch_manager(p, r, args=a.args)
        from . import launchwait
        proc.vstenv_window_watch = launchwait.watch_and_resync(p, v.manager_exe_names())
        print(f"{v.manager_name} started (pid {proc.pid})")
        if a.wait:
            proc.wait(); print(f"{v.manager_name} exited; finishing installs and bridging")
            v.finish_installs(p, r, since=t0); setup.after_change(p, r)
        else: _join_window_watch(proc)       # stay until the window is up, for the resync
    elif a.action == "apply-pending-update":
        # A manager whose self-updater this app disables can still be holding an
        # update it downloaded before that: it will never install it itself, and
        # installing it through this app is the only way it lands patched.
        p = _prefix()
        f = v.pending_manager_update(p)
        if f is None:
            print(f"{v.manager_name} has no pending update of its own"); return
        print(f"Installing the update {v.manager_name} downloaded: {Path(f).name}")
        if not v.apply_pending_manager_update(p, r): sys.exit("nothing was installed")
        _fail_if(r); setup.after_change(p, r)
        print(f"{v.manager_name} updated and patched")
    elif a.action == "repair":
        p = _prefix(); v.repair_manager(p, r); _fail_if(r)
    else:
        p = _prefix()
        print(f"{v.manager_name}: {'installed ' + (v.manager_version(p) or '') if v.manager_installed(p) else 'not installed'}")
        n = v.manager_notice(p)
        if n: print(n)
        for k, val in v.status(p).items(): print(f"  {k:<22} {val}")

def cmd_wedge(a):
    """The DirectComposition wedge: every dcomp GUI hangs until Wine restarts."""
    from . import wedge
    p = _prefix(); r = ConsoleReporter()
    if a.action == "clear":
        wedge.clear(p, r); return
    if wedge.probe_session(p) is False:
        print("The Wine session is wedged: no new program can open a window (a probe hung")
        print("waiting for the session's desktop). Run `vstenv wedge clear` (this closes")
        print("running Windows programs, a DAW's plugins included)."); return
    stuck = wedge.stuck_processes(p)
    if not stuck:
        print("No DirectComposition program is hung, and a new window can open."); return
    conclusive = wedge.detect(p)
    for pid in stuck:
        print(f"  pid {pid}: windowless, no CPU, dcomp.dll loaded")
    if conclusive:
        print("\nThis is the session wedge: every DirectComposition program stays hung")
        print("until the Wine session restarts. Run `vstenv wedge clear` (this closes")
        print("running Windows programs, a DAW's plugins included).")
    else:
        print("\nToo early to call it the session wedge -- a program that is still")
        print("starting looks the same. Re-run in a minute if it has not drawn a window.")

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

def _install_plugin_files(p, src, r) -> Path | None:
    """Plugins without an installer (an archive, a .vst3/.clap/.dll, a folder):
    copy them in. Returns an installer found in an archive instead, else None."""
    from . import plugfiles
    res = plugfiles.install(p, src, r)
    for n, why in res.skipped: print(f"  left out {n}: {why}")
    if res.installed:
        for fmt, t in res.installed: print(f"  {fmt}: {t}")
        return None
    if len(res.installers) == 1:
        print(f"  {src.name} holds no plugins but an installer: running {res.installers[0].name}")
        return res.installers[0]
    if res.installers:
        sys.exit(f"{src.name} holds several installers; run the one you want:\n" + "\n".join(f"  {APP_NAME} install '{x}'" for x in res.installers))
    sys.exit(f"no 64-bit Windows plugin (.vst3, .clap or VST2 .dll) or installer found in {src.name}")

def cmd_install(a):
    p = _prefix(); r = ConsoleReporter(); src = Path(a.installer)
    if not a.generic:
        from . import plugfiles
        if plugfiles.handles(src):
            src = _install_plugin_files(p, src, r)
            if src is None:
                if not a.no_sync: setup.after_change(p, r)
                return
    kind, v = ("program", None) if a.generic else vendors.classify_installer(src)
    if kind == "manager":
        print(f"  recognized: the {v.manager_name} installer")
        v.install_manager(p, src, r)
    elif kind == "product":
        print(f"  recognized: a {v.name} product installer")
        res = v.install_product(p, src, r, keep_trace=a.keep_trace)
        print(f"  {res['name']}: installed via {res['method']}")
    else:
        remedy = None
        if a.remedy:
            from . import remedies
            remedy = {}
            for kv in a.remedy:
                if "=" not in kv: sys.exit(f"--remedy takes key=value (have: {', '.join(remedies.KNOBS)})")
                k, v = kv.split("=", 1)
                if k not in remedies.KNOBS or v not in remedies.KNOBS[k]:
                    sys.exit(f"{k} can be {', '.join(remedies.KNOBS.get(k, ()))}; see: {APP_NAME} recipe --help")
                remedy[k] = v
        try:
            programs.install(p, src, r, remedy=remedy)
        except programs.InstallFailed as e:
            # Nothing was installed, so there is nothing to bridge or sync; the
            # environment pass would only make a failure look like a success.
            from . import remedies
            print(f"  {e}")
            for line in e.diagnosis: print(f"  {line}")
            if e.log: print(f"  its output: {e.log}")
            print("  Things to try (each re-runs it with one change):")
            for rid, lab, when, settings, suggested in remedies.options(e.tech, e.exit_code, e.log_tail()):
                print(f"    --remedy {' '.join(f'{k}={v}' for k, v in settings.items()):<24} {lab}{'  (fits this failure)' if suggested else ''}")
            print(f"  Share it: {APP_NAME} recipe share install {src.name} --failed --symptom \"...\"")
            raise SystemExit(1)
        if remedy:
            recipes.set_choice(p, "installs", src.name, **remedy)
            print(f"  kept for {src.name}: {remedies.label(remedy)}. Share it: {APP_NAME} recipe share install {src.name}")
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

def cmd_unmanaged(a):
    p = _prefix(); prog = programs.find(p, a.name)
    if not unmanaged.supported(prog):
        sys.exit(f"{prog.name} has no launcher, so there is no program to set this for")
    if a.state is None:
        print(f"{prog.name} ({unmanaged.exe_name(prog)}): "
              f"{'the window manager is kept away' if unmanaged.is_on(p, prog) else 'managed by the window manager'}")
        return
    on = a.state == "on"
    changed = unmanaged.set_for(p, prog, on)
    print(f"{prog.name}: {'the window manager is kept away' if on else 'handed back to the window manager'}"
          f"{'' if changed else ' (already)'}")
    if changed: print("Restart the program to see the change.")

def _recipe_target(p, kind: str, name: str):
    """(key the recipe is stored under, display name, program or None, exe path or None)."""
    if kind == "program":
        prog = programs.find(p, name)
        if not prog.exe: sys.exit(f"{prog.name} has no launcher, so there is nothing to set this for")
        exe = prog.exe.rsplit("\\", 1)[-1]
        return exe, prog.name, prog, p.to_host(prog.exe)
    if kind == "install":
        return Path(name).name, Path(name).name, None, None      # an installer file: its name is the key
    names = [b["name"] for b in yabridge.bridged(p)]
    hits = [n for n in names if n.lower() == name.lower()] or [n for n in names if name.lower() in n.lower()]
    if not hits: sys.exit(f"no bridged plugin matches '{name}' (see: {APP_NAME} plugins)")
    if len(hits) > 1: sys.exit(f"'{name}' matches several plugins: {', '.join(hits)}")
    return hits[0], hits[0], None, None

def cmd_recipe(a):
    p = _prefix()
    kind = {"program": "programs", "plugin": "plugins", "install": "installs"}.get(a.kind, a.kind)
    if a.action == "list":
        lines = recipes.in_effect(p)
        print("\n".join(lines) if lines else "no recipes in effect: everything runs with what the app detected")
        return
    if a.action == "update":
        recipes.update(ConsoleReporter()); return
    if not a.kind or not a.name: sys.exit(f"usage: {APP_NAME} recipe {a.action} program|plugin <name> ...")
    key, label, prog, exe_path = _recipe_target(p, a.kind, a.name)
    if a.action == "show":
        for knob, (v, who) in recipes.effective(p, kind, key).items():
            print(f"{label}: {knob} = {v} ({'your choice' if who == 'you' else 'from a recipe' if who == 'recipe' else 'detected'})")
        return
    if a.action == "reset":
        changed = recipes.reset(p, kind, key)
        if kind == "programs": recipes.apply_program(p, key, {}, ConsoleReporter(), exe_path)
        elif kind == "plugins": setup.after_change(p, ConsoleReporter(), force=True)
        print(f"{label}: back to what the app detects{'' if changed else ' (nothing was set)'}"); return
    if a.action == "set":
        settings = {}
        for kv in a.settings:
            if "=" not in kv: sys.exit(f"settings look like rendering=wine (have: {', '.join(recipes.KNOBS[kind])})")
            k, v = kv.split("=", 1); settings[k] = v
        try: cur = recipes.set_choice(p, kind, key, **settings)
        except ValueError as e: sys.exit(str(e))
        said = ", ".join(f"{k}={v}" for k, v in cur.items()) or "back to detected"
        if kind == "programs":
            recipes.apply_program(p, key, cur, ConsoleReporter(), exe_path)
            print(f"{label}: {said}. Restart it to see the change.")
        elif kind == "installs":
            print(f"{label}: {said}. Used the next time this file is installed.")
        else:
            setup.after_change(p, ConsoleReporter(), force=True)
            print(f"{label}: {said}. Reload the plugin in your DAW to see the change.")
        return
    if a.action == "share":
        settings = {k: v for k, (v, who) in recipes.effective(p, kind, key).items() if who == "you"}
        url = recipes.share_url(p, kind, key, settings, a.symptom or "", prog.version if prog else None, worked=not a.failed)
        print("Open this link to send the recipe in (nothing is sent until you press Submit there):\n" + url)
        return

def cmd_run(a):
    p = _prefix(); r = ConsoleReporter()
    prog = programs.find(p, a.name)
    try: proc = programs.run(p, prog, r)
    except RuntimeError as e: sys.exit(str(e))
    if a.wait:
        proc.wait(); setup.after_change(p, r)           # the program may have installed plugins
    elif getattr(proc, "vstenv_watch", None) is not None:
        proc.vstenv_watch.join()     # the vendor is observing the program (e.g. rescuing
                                     # its installs); exiting now would kill that thread
        setup.after_change(p, r)     # it may have installed something: bridge and sync
    else:
        # A desktop-menu launch. Stay with the program, quietly: it may install
        # plugins through a service of its own while it is open (Softube
        # Central), and they are bridged as they appear, then once more at exit.
        _join_window_watch(proc)
        programs.watch_plugins(p, proc, lambda: setup.after_change(p, r))
        setup.after_change(p, r)

def _join_window_watch(proc, seconds=40.0):
    """Wait for the window watcher programs.run attached (it fixes a window the WM
    maximized on sight); the thread is a daemon, so exiting would kill it."""
    w = getattr(proc, "vstenv_window_watch", None)
    if w is not None: w.join(seconds)

def cmd_uninstall(a):
    p = _prefix(); r = ConsoleReporter()
    programs.uninstall(p, programs.find(p, a.name), r)
    if not a.no_sync: setup.after_change(p, r)

def cmd_menu(a):
    p = _prefix(); r = ConsoleReporter()
    if a.action == "remove":
        from . import filemanager
        menu.remove_all(r); filemanager.remove(r)
    else:
        from . import filemanager
        menu.sync(p, r); filemanager.apply(r)

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

def cmd_webview2(a):
    from . import webview2
    if a.action == "software-gl":
        p = _prefix()
        if a.value is None:
            print("on" if webview2.software_gl(p) else "off"); return
        r = ConsoleReporter(); done = webview2.set_software_gl(p, a.value == "on", r); _fail_if(r)
        if done: print(webview2.restart_note(done))
        return
    p = _prefix()
    if a.action == "status":
        st = webview2.status(p)
        print(f"WebView2 runtime: {'installed' if st['installed'] else 'not installed'}")
        if st["apps"]: print(f"  apps using it: {', '.join(st['apps'])}")
        return
    r = ConsoleReporter(); webview2.install(p, r); _fail_if(r)

def cmd_mono(a):
    from . import mono
    p = _prefix()
    if a.action == "status":
        st = mono.status(p); print(f"wine mono: {'installed' if st['installed'] else 'not installed'} (this wine wants {st['required']})"); return
    r = ConsoleReporter(); mono.install(p, r); _fail_if(r)

def cmd_wine_fixes(a):
    from . import winefixes
    b = wine.installed_build()
    if b is None: print("no wine build: run setup first"); sys.exit(1)
    if a.action == "status":
        st = winefixes.status(b); m = st["marker"] or {}
        print(f"patched DLLs: {', '.join(st['files']) if st['files'] else 'none'}" + (f" (from {m.get('source', '?')}, {m.get('installed', '?')})" if st["files"] else ""))
        if st["missing"]: print(f"missing: {', '.join(st['missing'])} -- vstenv wine-fixes install")
        return
    r = ConsoleReporter()
    if a.action == "remove": winefixes.remove(b, r)
    else: winefixes.install(b, r, force=a.force)
    _fail_if(r)

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
    s = sp.add_parser("manager", help="a vendor's manager application: install <file> | launch | repair | status | apply-pending-update")
    s.add_argument("vendor"); s.add_argument("action", choices=["install", "launch", "repair", "status", "apply-pending-update"]); s.add_argument("file", nargs="?")
    s.add_argument("--wait", action="store_true", help="launch: wait for it to exit, then finish installs and bridge"); s.add_argument("args", nargs="*"); s.set_defaults(f=cmd_manager)
    s = sp.add_parser("products", help="list what the vendors' managers installed"); s.add_argument("--vendor"); s.set_defaults(f=cmd_products)
    s = sp.add_parser("install", help="install any downloaded Windows installer, or plugins without one (a .zip, .vst3, .clap or VST2 .dll): a vendor's manager or product is recognized and installed with its fixes, anything else runs as a plain installer")
    s.add_argument("installer"); s.add_argument("--generic", action="store_true", help="run it interactively even if a vendor module recognises it")
    s.add_argument("--remedy", nargs="+", metavar="KEY=VALUE", help="run it with a remedy: silent=on, winver=win8, rendering=wine|gdi, disable_dll=netprofm, as_program=on")
    s.add_argument("--keep-trace", action="store_true"); s.add_argument("--no-sync", action="store_true"); s.set_defaults(f=cmd_install)
    s = sp.add_parser("sync", help="bridge the prefix's plugins to Linux DAWs with yabridge and refresh the menu"); s.add_argument("dirs", nargs="*", help="extra plugin directories"); s.set_defaults(f=cmd_sync)
    sp.add_parser("programs", help="list Windows programs installed in the prefix").set_defaults(f=cmd_programs)
    s = sp.add_parser("recipe", help="try, keep and share the settings a program or plugin can be given (rendering, window manager, browser)",
                      description=f"{recipes.UNNECESSARY}\n\nSettings: " + "; ".join(f"{k}: {', '.join(v)}" for k, v in recipes.KNOBS['programs'].items())
                                  + "\n\n" + "\n".join(f"rendering={k}: {v}" for k, v in recipes.RENDERING_TEXT.items()),
                      formatter_class=argparse.RawDescriptionHelpFormatter)
    s.add_argument("action", choices=["list", "show", "set", "reset", "share", "update"])
    s.add_argument("kind", nargs="?", choices=["program", "plugin", "install"])
    s.add_argument("name", nargs="?", help="program name (or part of it), a bridged plugin's file name, or an installer's file name")
    s.add_argument("settings", nargs="*", help="for set: rendering=wine wm_away=on browser_software_gl=on; for an install: silent=on winver=win8 ...")
    s.add_argument("--symptom", help="for share: what you saw, in a sentence")
    s.add_argument("--failed", action="store_true", help="for share: report that the setting did not help")
    s.set_defaults(f=cmd_recipe)
    s = sp.add_parser("unmanaged", help=f"{unmanaged.LABEL.lower()} for one program (menus that flash and close)",
                      description=unmanaged.EXPLAIN)
    s.add_argument("name", help="program name, or part of it")
    s.add_argument("state", nargs="?", choices=["on", "off"], help="omit to show the current setting")
    s.set_defaults(f=cmd_unmanaged)
    s = sp.add_parser("run", help="start an installed program (name or part of it)"); s.add_argument("name"); s.add_argument("--wait", action="store_true"); s.set_defaults(f=cmd_run)
    s = sp.add_parser("uninstall", help="run an installed program's own uninstaller"); s.add_argument("name"); s.add_argument("--no-sync", action="store_true"); s.set_defaults(f=cmd_uninstall)
    s = sp.add_parser("menu", help="desktop menu entries for the prefix's programs, and the file manager's \"Install with VST Environment\""); s.add_argument("action", nargs="?", default="update", choices=["update", "remove"]); s.set_defaults(f=cmd_menu)
    sp.add_parser("doctor", help="check every fix and prerequisite").set_defaults(f=cmd_doctor)
    sp.add_parser("status", help="yabridge status").set_defaults(f=cmd_status)
    s = sp.add_parser("mono", help=".NET runtime (Wine Mono) for programs that need it: install | status")
    s.add_argument("action", nargs="?", default="install", choices=["install", "status"]); s.set_defaults(f=cmd_mono)
    s = sp.add_parser("webview2", help="Microsoft WebView2 runtime for embedded-Edge apps (SINE, Audio Modeling): install | status")
    s.add_argument("action", nargs="?", default="install", choices=["install", "status", "software-gl"])
    s.add_argument("value", nargs="?", choices=["on", "off"], help="for software-gl: switch it (omit to show)")
    s.set_defaults(f=cmd_webview2)
    s = sp.add_parser("wine-fixes", help="patched Wine DLLs this app ships (DirectComposition for Steinberg): install | status | remove")
    s.add_argument("action", nargs="?", default="install", choices=["install", "status", "remove"]); s.add_argument("--force", action="store_true"); s.set_defaults(f=cmd_wine_fixes)
    s = sp.add_parser("dxvk", help="Direct3D on Vulkan for plugin GUIs that Wine draws wrong")
    s.add_argument("action", nargs="?", default="install", choices=["install", "remove", "status"]); s.add_argument("--force", action="store_true"); s.set_defaults(f=cmd_dxvk)
    sp.add_parser("prefixes", help="which vstenv prefixes exist, who owns a vendor daemon, what yabridge points at").set_defaults(f=cmd_prefixes)
    s = sp.add_parser("url-handlers", help="desktop handlers for the vendors' sign-in callback links"); s.add_argument("action", nargs="?", default="register", choices=["register", "unregister", "status"]); s.set_defaults(f=cmd_url_handlers)
    s = sp.add_parser("report", help="write a diagnostic bundle to send with a bug report"); s.add_argument("-o", "--output"); s.add_argument("--summary-only", action="store_true"); s.set_defaults(f=cmd_report)
    s = sp.add_parser("wedge", help="DirectComposition programs all hang windowless: show it, or restart the Wine session"); s.add_argument("action", nargs="?", default="status", choices=["status", "clear"]); s.set_defaults(f=cmd_wedge)
    sp.add_parser("rescue-install", help="an installer has stopped responding: stop it and finish the install from its payload").set_defaults(f=cmd_rescue)
    s = sp.add_parser("finish-installs", help="complete installs a vendor's manager started but did not finish"); s.add_argument("--no-sync", action="store_true"); s.set_defaults(f=cmd_finish_installs)
    a = ap.parse_args(argv); a.f(a)

if __name__ == "__main__": main()
