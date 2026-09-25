"""GTK4 / libadwaita front end. Thin: every action calls the package."""
import os, sys, threading, time, traceback
from pathlib import Path
import gi
gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk, Gio
from . import __version__, APP_ID, APP_NAME, paths, wine, yabridge, doctor, programs, setup, vendors, menu, runtime
from .progress import Reporter, OK, FAIL, SKIP, RUN

TITLE = "VST Environment"

def ui(fn, *a):
    """Run fn(*a) on the main loop."""
    GLib.idle_add(lambda: (fn(*a), False)[1])

class GuiReporter(Reporter):
    """Feeds a step list + log view on the main loop."""
    ICON = {OK: "emblem-ok-symbolic", FAIL: "dialog-error-symbolic", SKIP: "radio-symbolic", RUN: "content-loading-symbolic"}
    def __init__(self, group: Adw.PreferencesGroup, log: Gtk.TextBuffer, progress: Gtk.ProgressBar | None = None):
        super().__init__(); self.group, self.logbuf, self.bar = group, log, progress; self.rows = {}
        self._downloading = False
        self.on_log_line = None
    def on_step(self, s):
        def go():
            row = self.rows.get(id(s))
            if row is None:
                row = Adw.ActionRow(title=GLib.markup_escape_text(s.name)); img = Gtk.Image(); row.add_suffix(img); row.img = img
                self.group.add(row); self.rows[id(s)] = row
            row.img.set_from_icon_name(self.ICON[s.status]); row.set_subtitle(GLib.markup_escape_text(s.detail or ""))
            if s.status == FAIL: row.add_css_class("error")
            # Only downloads report bytes; pulse otherwise and name the running step.
            if self.bar is not None and not self._downloading:
                if s.status == RUN: self.bar.set_text(s.name); self.bar.pulse()
                else:
                    done = sum(1 for x in self.steps if x.status != RUN)
                    self.bar.set_text(f"{done} step{'s' if done != 1 else ''} done")
        ui(go)
    def on_log(self, line):
        def go():
            self.logbuf.insert(self.logbuf.get_end_iter(), line + "\n")
            if self.on_log_line is not None: self.on_log_line()
        ui(go)
    def on_progress(self, done, total, label):
        if not self.bar or not total: return
        self._downloading = done < total
        ui(lambda: (self.bar.set_fraction(done / total), self.bar.set_text(f"{label} {done/1e6:.0f}/{total/1e6:.0f} MB")))

class TaskPage(Gtk.Box):
    """Step list + progress bar + collapsible log; used by setup and installs."""
    def __init__(self, title):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=18, margin_bottom=18, margin_start=24, margin_end=24)
        self.group = Adw.PreferencesGroup(title=title); self.append(self.group)
        self.bar = Gtk.ProgressBar(show_text=True); self.append(self.bar)
        self.logbuf = Gtk.TextBuffer(); tv = Gtk.TextView(buffer=self.logbuf, editable=False, monospace=True)
        sw = Gtk.ScrolledWindow(min_content_height=140, child=tv)
        self.details = Gtk.Expander(label="Details", child=sw, visible=False); self.append(self.details)
        self.reporter = GuiReporter(self.group, self.logbuf, self.bar)
        self.reporter.on_log_line = lambda: self.details.set_visible(True)
    def reset(self, title=None):
        for r in list(self.reporter.rows.values()): self.group.remove(r)
        self.reporter.rows.clear(); self.reporter.steps.clear(); self.bar.set_fraction(0); self.bar.set_text("")
        self.reporter._downloading = False
        self.logbuf.set_text(""); self.details.set_visible(False)
        if title: self.group.set_title(title)

def _row(title, subtitle, icon, cb):
    r = Adw.ActionRow(title=title, subtitle=subtitle, activatable=True)
    r.add_suffix(Gtk.Image(icon_name=icon)); r.connect("activated", lambda *_: cb()); return r

class Window(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title=TITLE, default_width=880, default_height=660)
        self.toasts = Adw.ToastOverlay(); self.set_content(self.toasts)
        self.busy = False; self._rows = {}
        tv = Adw.ToolbarView(); self.toasts.set_child(tv)
        self.stack = Adw.ViewStack()
        header = Adw.HeaderBar(); switcher = Adw.ViewSwitcherTitle(stack=self.stack, title=TITLE); header.set_title_widget(switcher)
        m = Gio.Menu(); m.append("Re-run setup / repair", "app.setup"); m.append("Update the app menu", "app.menu"); m.append("About", "app.about")
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=m))
        tv.add_top_bar(header); tv.set_content(self.stack)
        self.stack.add_titled_with_icon(self.build_plugins(), "plugins", "Plugins", "audio-x-generic-symbolic")
        self.stack.add_titled_with_icon(self.build_programs(), "programs", "Programs", "application-x-executable-symbolic")
        self.stack.add_titled_with_icon(self.build_install(), "install", "Install", "list-add-symbolic")
        self.stack.add_titled_with_icon(self.build_health(), "health", "Health", "emblem-ok-symbolic")
        self.task = TaskPage("Working")
        self.task_page = self.stack.add_titled_with_icon(self.task, "task", "Progress", "content-loading-symbolic")
        self.task_page.set_visible(False)
        b = wine.installed_build(); self.prefix = wine.Prefix(paths.PREFIX, b) if b else None
        if not self.is_ready(): self.run_setup(first=True)
        else: self.refresh_all()
        if os.environ.get("VSTENV_PAGE"): self.stack.set_visible_child_name(os.environ["VSTENV_PAGE"])
        act = os.environ.get("VSTENV_ACTION")   # test hook: run an action at startup
        if act: GLib.timeout_add(500, lambda: ({"sync": self.sync, "setup": self.run_setup}[act](), False)[1])

    # ---- state ------------------------------------------------------------------
    def is_ready(self): return self.prefix is not None and self.prefix.exists and runtime.status(self.prefix)["prepared"]
    def toast(self, text): ui(lambda: self.toasts.add_toast(Adw.Toast(title=text, timeout=4)))
    def run_bg(self, title, fn, done=None):
        """Run fn(reporter) in a thread on the Progress page."""
        if self.busy: self.toast("Another task is still running"); return
        self.busy = True; self.task.reset(title); self.task_page.set_visible(True); self.stack.set_visible_child_name("task")
        def worker():
            err = None
            try: result = fn(self.task.reporter)
            except Exception as e:
                err = e; result = None
                for line in traceback.format_exc().rstrip().splitlines(): self.task.reporter.log(line)
            def finish():
                self.busy = False
                if err: self.task.reporter.step(f"Error: {err}"); self.task.reporter.fail()
                if done: done(result, err)
                self.refresh_all()
                if err is None: GLib.timeout_add_seconds(3, self._hide_task_page)
            ui(finish)
        threading.Thread(target=worker, daemon=True).start()
    def _hide_task_page(self):
        if not self.busy and self.stack.get_visible_child_name() == "task": self.stack.set_visible_child_name("plugins")
        if not self.busy: self.task_page.set_visible(False)
        return False
    def _fill(self, group, rows):
        for r in self._rows.get(group, []): group.remove(r)
        self._rows[group] = []
        for r in rows: group.add(r); self._rows[group].append(r)

    # ---- setup ---------------------------------------------------------------------
    def run_setup(self, first=False):
        def fn(r):
            b = wine.provision(r); self.prefix = wine.Prefix(paths.PREFIX, b); setup.setup(self.prefix, r); return True
        def done(res, err):
            if err or self.task.reporter.failed: return
            self.toast("Environment ready"); self.stack.set_visible_child_name("install")
        self.run_bg("Preparing the environment" if first else "Repairing setup", fn, done)
    def after_change(self, title="Finishing up"):
        self.run_bg(title, lambda r: setup.after_change(self.prefix, r))

    # ---- plugins page ------------------------------------------------------------------
    def build_plugins(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.daw_banner = Adw.Banner(revealed=False); box.append(self.daw_banner)
        page = Adw.PreferencesPage(vexpand=True); box.append(page)
        self.product_groups = {}
        for v in vendors.all():
            g = Adw.PreferencesGroup(title=f"{v.name} products", description=f"Installed through {v.manager_name}." if v.manager_name else "")
            page.add(g); self.product_groups[v.id] = g
        self.bridged_group = Adw.PreferencesGroup(title="Bridged plugins", description="Available to Linux DAWs through yabridge (~/.vst, ~/.vst3, ~/.clap).")
        page.add(self.bridged_group)
        return box
    def refresh_plugins(self):
        if not self.is_ready(): return
        def work():
            prods = {v.id: v.products(self.prefix) for v in vendors.all()}
            bridged = yabridge.bridged(self.prefix)
            state, detail = yabridge.plugin_wine_status(self.prefix)
            def show():
                if state == "missing": self.daw_banner.set_title("Plugins are not bridged: a DAW would run them with the host's wine and damage the prefix — run Re-run setup / repair.")
                self.daw_banner.set_revealed(state != "active")
                for vid, g in self.product_groups.items():
                    rows = []
                    for x in prods.get(vid, []):
                        row = Adw.ActionRow(title=GLib.markup_escape_text(x.name), subtitle=GLib.markup_escape_text(f"{x.kind or '?'} {x.version}".strip()))
                        flags = [("registered" if x.registered else "not registered", x.registered)]
                        if x.licensed is not None: flags.append(("licensed" if x.licensed else "no license", x.licensed))
                        for txt, ok in flags: row.add_suffix(Gtk.Label(label=txt, css_classes=["caption", "dim-label" if ok else "warning"]))
                        rows.append(row)
                    g.set_visible(bool(rows)); self._fill(g, rows)
                if not any(prods.values()):
                    g = next(iter(self.product_groups.values()), None)
                    if g: g.set_visible(True); self._fill(g, [Adw.ActionRow(title="Nothing installed yet", subtitle="Open a vendor's manager from the Install tab")])
                brows = [Adw.ActionRow(title=GLib.markup_escape_text(b["name"]), subtitle=GLib.markup_escape_text(b["info"])) for b in bridged]
                if not brows: brows.append(Adw.ActionRow(title="No bridged plugins yet", subtitle="Install a product, then Bridge plugins now"))
                self._fill(self.bridged_group, brows)
            ui(show)
        threading.Thread(target=work, daemon=True).start()

    # ---- programs page ------------------------------------------------------------------
    def build_programs(self):
        page = Adw.PreferencesPage()
        self.programs_group = Adw.PreferencesGroup(title="Installed programs", description="Everything with an installer record or a Start Menu shortcut in the prefix, plus the vendors' managers. Each also appears in your desktop's application menu.")
        page.add(self.programs_group)
        g = Adw.PreferencesGroup(title="Install", description=programs.LIMITS)
        g.add(_row("Install a Windows program", "Pick a .exe or .msi installer; its own window opens. Plugins it installs are bridged when it finishes.", "document-open-symbolic",
                   lambda: self.pick_file("Choose installer (.exe or .msi)", self.install_program, downloads=True)))
        g.add(_row("Refresh the list and the app menu", "", "view-refresh-symbolic", lambda: self.run_bg("Updating the app menu", lambda r: menu.sync(self.prefix, r))))
        page.add(g); return page
    def refresh_programs(self):
        if not self.is_ready(): return
        def work():
            progs = programs.installed(self.prefix)
            def show():
                rows = []
                for x in progs:
                    sub = " · ".join(s for s in (x.version, x.publisher, x.exe or "no launcher known (uninstall only)") if s)
                    row = Adw.ActionRow(title=GLib.markup_escape_text(x.name), subtitle=GLib.markup_escape_text(sub))
                    if x.exe:
                        b = Gtk.Button(icon_name="media-playback-start-symbolic", valign=Gtk.Align.CENTER, tooltip_text="Run", css_classes=["flat"])
                        b.connect("clicked", lambda *_, prog=x: self.run_program(prog)); row.add_suffix(b)
                    if x.uninstall:
                        b = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER, tooltip_text="Uninstall", css_classes=["flat"])
                        b.connect("clicked", lambda *_, prog=x: self.uninstall_program(prog)); row.add_suffix(b)
                    rows.append(row)
                if not rows: rows.append(Adw.ActionRow(title="No programs found", subtitle="Install one below, or a vendor's manager from the Install tab"))
                self._fill(self.programs_group, rows)
            ui(show)
        threading.Thread(target=work, daemon=True).start()
    def run_program(self, prog):
        v = vendors.for_program(prog)
        if v is not None and v.is_manager_program(prog): return self.open_manager(v)
        try: proc = programs.run(self.prefix, prog); self.toast(f"{prog.name} is starting…")
        except Exception as e: self.toast(str(e)); return
        def watch():           # a program may install plugins while it runs: bridge whatever appeared
            proc.wait(); ui(lambda: self.after_change(f"Bridging plugins after {prog.name}"))
        threading.Thread(target=watch, daemon=True).start()
    def uninstall_program(self, prog):
        self.run_bg(f"Uninstalling {prog.name}", lambda r: (programs.uninstall(self.prefix, prog, r), setup.after_change(self.prefix, r)))
    def install_program(self, path):
        self.run_bg(f"Installing {path.name}", lambda r: (programs.install(self.prefix, path, r), setup.after_change(self.prefix, r)))

    # ---- install page -------------------------------------------------------------------
    def build_install(self):
        page = Adw.PreferencesPage(); self.manager_rows = {}
        for v in vendors.with_manager():
            g = Adw.PreferencesGroup(title=v.name, description=f"Products from your {v.name} account.")
            g.add(_row(f"Open {v.manager_name}", "Sign in, install or update products. New plugins are bridged while it runs and when you close it.", "go-next-symbolic", lambda v=v: self.open_manager(v)))
            g.add(_row(f"Get {v.manager_name} from {v.name}", v.download_page or "", "web-browser-symbolic", lambda v=v: self.open_url(v.download_page)))
            r = _row(f"Install or update {v.manager_name} from a downloaded installer", f"Pick {v.installer_hint}.", "document-open-symbolic",
                     lambda v=v: self.pick_file(f"Choose the {v.manager_name} installer", lambda f, v=v: self.install_manager(v, f), downloads=True))
            self.manager_rows[v.id] = r; g.add(r)
            if type(v).install_product is not vendors.Vendor.install_product:
                g.add(_row(f"Install a {v.name} product from its installer", f"When {v.manager_name}'s own install fails: the setup is driven silently and finished by hand if it stops.", "document-open-symbolic",
                           lambda v=v: self.pick_file(f"Choose a {v.name} product installer", lambda f, v=v: self.install_product(v, f))))
            page.add(g)
        g = Adw.PreferencesGroup(title="Other plugins", description="Any Windows VST2 / VST3 / CLAP installer.")
        g.add(_row("Run a plugin installer", "The installer's own window opens; plugins are bridged when it finishes.", "document-open-symbolic", lambda: self.pick_file("Choose installer", self.install_program)))
        g.add(_row("Add a plugin folder", "If an installer put VST2 .dlls somewhere unusual inside the prefix.", "folder-open-symbolic", self.pick_folder))
        g.add(_row("Bridge plugins now", "Re-scan the prefix and update the DAW-visible plugins.", "view-refresh-symbolic", self.sync))
        g.add(_row("Finish interrupted installs", "Complete an install a vendor's manager started but did not finish (Health lists them).", "emblem-synchronizing-symbolic", self.finish_installs))
        page.add(g); return page
    def open_url(self, url):
        if url: Gtk.UriLauncher(uri=url).launch(self, None, lambda l, res: l.launch_finish(res))
    def install_manager(self, v, path):
        def fn(r):
            b = wine.provision(r); self.prefix = self.prefix or wine.Prefix(paths.PREFIX, b)
            v.install_manager(self.prefix, path, r); setup.after_change(self.prefix, r); return True
        def done(res, err):
            if not err and not self.task.reporter.failed: self.toast(f"{v.manager_name} installed — open it to sign in"); self.stack.set_visible_child_name("install")
        self.run_bg(f"Installing {v.manager_name} from {path.name}", fn, done)
    def install_product(self, v, path):
        def fn(r):
            res = v.install_product(self.prefix, path, r); setup.after_change(self.prefix, r); return res
        self.run_bg(f"Installing {path.name}", fn, lambda res, err: self.toast(f"{res['name']} installed ({res['method']})") if res else None)
    def open_manager(self, v):
        if not self.is_ready(): self.toast("Run setup first"); return
        if not v.manager_installed(self.prefix): self.toast(f"{v.manager_name} is not installed yet — get it from {v.name} and pick the installer here"); return
        try: proc = v.launch_manager(self.prefix)
        except Exception as e: self.toast(str(e)); return
        self.toast(f"{v.manager_name} is starting…"); t0 = time.time()
        def watch():
            # While the manager runs: rescue an install it drives that stopped making
            # progress, and bridge plugins as they appear (people install a product and
            # leave the manager open, then wonder why the DAW cannot see it).
            w = None
            try: seen = yabridge.plugin_signature(self.prefix)
            except Exception: seen = None
            while proc.poll() is None:
                try:
                    w, stalled, pids = v.watch_installs(self.prefix, w)
                    if stalled and pids:
                        w = None
                        ui(lambda: self.run_bg("An installer stopped responding — finishing it",
                                               lambda r: (v.rescue_installs(self.prefix, r), setup.after_change(self.prefix, r))))
                except Exception: pass
                try:
                    if seen is not None:
                        now = yabridge.plugin_signature(self.prefix)
                        if now != seen and now:
                            added = len(now - seen); seen = now
                            if added: ui(lambda n=added: self.after_change(f"Bridging {n} newly installed plugin{'s' if n != 1 else ''}"))
                except Exception: pass
                time.sleep(15)
            proc.wait()
            if time.time() - t0 < 15: self.toast(f"{v.manager_name} is already open"); return
            ui(lambda: self.run_bg(f"Finishing up after {v.manager_name}", lambda r: (v.finish_installs(self.prefix, r), setup.after_change(self.prefix, r))))
        threading.Thread(target=watch, daemon=True).start()
    def pick_file(self, title, cb, downloads=False):
        d = Gtk.FileDialog(title=title)
        dl = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOWNLOAD)
        if downloads and dl: d.set_initial_folder(Gio.File.new_for_path(dl))
        def on(dlg, res):
            try: f = dlg.open_finish(res)
            except GLib.Error: return
            cb(Path(f.get_path()))
        d.open(self, None, on)
    def pick_folder(self):
        d = Gtk.FileDialog(title="Choose plugin folder", initial_folder=Gio.File.new_for_path(str(self.prefix.drive_c / "Program Files")))
        def on(dlg, res):
            try: f = dlg.select_folder_finish(res)
            except GLib.Error: return
            self.run_bg("Bridging plugins", lambda r: yabridge.sync(self.prefix, r, extras=[f.get_path()]))
        d.select_folder(self, None, on)
    def sync(self): self.after_change("Bridging plugins")
    def finish_installs(self):
        def fn(r):
            res = []
            for v in vendors.all(): res += v.finish_installs(self.prefix, r)
            if not res: r.step("Interrupted installs"); r.skip("none found")
            setup.after_change(self.prefix, r); return res
        self.run_bg("Finishing interrupted installs", fn)

    # ---- health page --------------------------------------------------------------------
    def build_health(self):
        page = Adw.PreferencesPage()
        self.health_group = Adw.PreferencesGroup(title="Checks"); page.add(self.health_group)
        g = Adw.PreferencesGroup(title="Sign-in")
        g.add(_row("Fix browser sign-in", "A vendor's manager may sign in through your browser; this makes the link come back to it.", "system-users-symbolic",
                   lambda: self.run_bg("Registering the sign-in handlers", lambda rep: __import__(f"{APP_NAME}.urlschemes", fromlist=["x"]).register_all(self.prefix, rep))))
        page.add(g)
        g = Adw.PreferencesGroup(title="Reporting a problem", description="If a check above cannot be fixed, send this with your report.")
        g.add(_row("Save a diagnostic report", "Writes a file to your home folder: this app's state and logs, with serials, licence tokens and your home path removed.", "document-save-symbolic", self.save_report))
        page.add(g); return page
    def save_report(self):
        from . import report
        def fn(r):
            r.step("Collecting diagnostics"); dest = report.write_bundle(p=self.prefix); r.ok(str(dest)); return dest
        self.run_bg("Saving a diagnostic report", fn)
    def refresh_health(self):
        token = self._health_token = getattr(self, "_health_token", 0) + 1
        def add_row(c):
            if token != self._health_token: return
            if not self._health_started:
                self._health_started = True; self._fill(self.health_group, [])
            row = Adw.ActionRow(title=GLib.markup_escape_text(c.name), subtitle=GLib.markup_escape_text(c.detail + (f"  ·  fix: {c.fix}" if not c.ok and c.fix else "")))
            row.add_suffix(Gtk.Image(icon_name="emblem-ok-symbolic" if c.ok else "dialog-warning-symbolic"))
            self.health_group.add(row); self._rows.setdefault(self.health_group, []).append(row)
        self._health_started = False
        spinner = Adw.ActionRow(title="Checking…", subtitle="Starting Wine and probing the prefix; results appear as they finish.")
        spinner.add_suffix(Gtk.Spinner(spinning=True)); self._fill(self.health_group, [spinner])
        def work():
            doctor.run(self.prefix, on_check=lambda c: ui(lambda c=c: add_row(c)))
            ui(lambda: (token == self._health_token and not self._health_started) and self._fill(self.health_group, []))
        threading.Thread(target=work, daemon=True).start()
    def refresh_notices(self):
        if not self.is_ready(): return
        def work():
            notes = {v.id: v.manager_notice(self.prefix) for v in vendors.with_manager()}
            def show():
                for vid, n in notes.items():
                    v = vendors.get(vid); row = self.manager_rows.get(vid)
                    if row: row.set_subtitle(GLib.markup_escape_text(n or f"Pick {v.installer_hint}."))
            ui(show)
        threading.Thread(target=work, daemon=True).start()
    def refresh_all(self): self.refresh_plugins(); self.refresh_programs(); self.refresh_health(); self.refresh_notices()

class App(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID)
        for name, cb in (("setup", lambda *_: self.win.run_setup()), ("about", self.about),
                         ("menu", lambda *_: self.win.run_bg("Updating the app menu", lambda r: menu.sync(self.win.prefix, r)))):
            act = Gio.SimpleAction(name=name); act.connect("activate", cb); self.add_action(act)
        for i, page in enumerate(("plugins", "programs", "install", "health"), start=1):
            act = Gio.SimpleAction(name=f"tab{i}"); act.connect("activate", lambda *_, p=page: self.win.stack.set_visible_child_name(p))
            self.add_action(act); self.set_accels_for_action(f"app.tab{i}", [f"<Control>{i}"])
    def do_activate(self):
        self.win = Window(self); self.win.present()
    def about(self, *_):
        Adw.AboutWindow(transient_for=self.win, application_name=TITLE, version=__version__,
                        comments="A managed environment for Windows audio plugins on Linux: vendor managers, their products, and VST bridging — without touching Wine yourself.").present()

def main():
    paths.ensure_dirs(); sys.exit(App().run(sys.argv))

if __name__ == "__main__": main()
