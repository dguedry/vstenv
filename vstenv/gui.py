"""GTK4 / libadwaita front end. Thin: every action calls the package."""
import os, sys, threading, time, traceback
from pathlib import Path
import gi
gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk, Gio
from . import __version__, APP_ID, APP_NAME, paths, wine, yabridge, doctor, programs, setup, vendors, menu, runtime, dcomp
from .progress import Reporter, OK, FAIL, SKIP, RUN
from .filtering import Filter

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
                row = Adw.ActionRow(title=GLib.markup_escape_text(s.name)); img = Gtk.Image(); spin = Gtk.Spinner()
                row.add_suffix(spin); row.add_suffix(img); row.img, row.spin = img, spin
                self.group.add(row); self.rows[id(s)] = row
            # A running step spins (a long step must visibly be alive); a finished one shows its outcome.
            running = s.status == RUN
            row.spin.set_visible(running); row.spin.set_spinning(running); row.img.set_visible(not running)
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
        # The step list scrolls: a long pass adds a row per step, and without a
        # scroller each row raised the window's MINIMUM height until it ran off
        # the bottom of the screen and could not be resized back.
        self.group = Adw.PreferencesGroup(title=title)
        self.append(Gtk.ScrolledWindow(child=self.group, vexpand=True, min_content_height=220,
                                       hscrollbar_policy=Gtk.PolicyType.NEVER))
        self.bar = Gtk.ProgressBar(show_text=True); self.append(self.bar)
        self.logbuf = Gtk.TextBuffer(); tv = Gtk.TextView(buffer=self.logbuf, editable=False, monospace=True)
        sw = Gtk.ScrolledWindow(min_content_height=140, child=tv)
        self.details = Gtk.Expander(label="Details", child=sw, visible=False); self.append(self.details)
        self.reporter = GuiReporter(self.group, self.logbuf, self.bar)
        self.reporter.on_log_line = lambda: self.details.set_visible(True)
        self._pulse = None
    def start(self):
        """Keep the bar moving for as long as the task runs: steps can take minutes
        without an event, and a still bar reads as a hang."""
        if self._pulse is None:
            self._pulse = GLib.timeout_add(150, lambda: (not self.reporter._downloading and self.bar.pulse(), True)[1])
    def stop(self):
        if self._pulse is not None: GLib.source_remove(self._pulse); self._pulse = None
    def reset(self, title=None):
        for r in list(self.reporter.rows.values()): self.group.remove(r)
        self.reporter.rows.clear(); self.reporter.steps.clear(); self.bar.set_fraction(0); self.bar.set_text("")
        self.reporter._downloading = False
        self.logbuf.set_text(""); self.details.set_visible(False)
        if title: self.group.set_title(title)

def _row(title, subtitle, icon, cb, tooltip=None):
    r = Adw.ActionRow(title=title, subtitle=subtitle, activatable=True)
    if tooltip: r.set_tooltip_text(tooltip)
    r.add_suffix(Gtk.Image(icon_name=icon)); r.connect("activated", lambda *_: cb()); return r

def _search_bar(on_change, placeholder="Search by name, vendor or format"):
    """A search entry across the top of a page. Every keystroke re-draws the
    list from a Filter (the pattern Cabinet's library page uses). Returns
    (widget, entry)."""
    entry = Gtk.SearchEntry(placeholder_text=placeholder, hexpand=True)
    entry.connect("search-changed", lambda *_: on_change())
    box = Gtk.Box(margin_top=12, margin_start=12, margin_end=12)
    box.append(entry)
    return Adw.Clamp(child=box), entry

PILL_CLASS = {"works": "success", "patched": "warning", "limited": "warning", "cannot": "error"}
def _pill(level):
    """A small coloured word on the right of a row: works / patched / limited / cannot run."""
    return Gtk.Label(label=vendors.LEVEL_LABELS.get(level, level), valign=Gtk.Align.CENTER,
                     css_classes=["caption", "pill", PILL_CLASS.get(level, "dim-label")], tooltip_text="Compatibility under Wine: click the row for the note")

def _noted_row(title, subtitle, note, suffixes=()):
    """An ActionRow, or, when there is a note, an ExpanderRow with the pill on the
    right and the sentence inside. Takes no more height until expanded."""
    if note is None:
        r = Adw.ActionRow(title=title, subtitle=subtitle)
        for w in suffixes: r.add_suffix(w)
        return r
    r = Adw.ExpanderRow(title=title, subtitle=subtitle)
    for w in suffixes: r.add_suffix(w)
    r.add_suffix(_pill(note.level))
    inner = Adw.ActionRow(title=GLib.markup_escape_text(note.text), title_lines=0); inner.add_css_class("property")
    r.add_row(inner)
    return r

def _menu_row(title, subtitle, icon, cb, items):
    """A row with a "…" button opening a popover of the less common actions."""
    r = _row(title, subtitle, icon, cb)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, margin_top=6, margin_bottom=6, margin_start=6, margin_end=6)
    pop = Gtk.Popover(child=box)
    for label, fn in items:
        b = Gtk.Button(label=label, css_classes=["flat"], halign=Gtk.Align.FILL); b.get_child().set_halign(Gtk.Align.START)
        b.connect("clicked", lambda *_, fn=fn: (pop.popdown(), fn())); box.append(b)
    r.add_suffix(Gtk.MenuButton(icon_name="view-more-symbolic", popover=pop, valign=Gtk.Align.CENTER, css_classes=["flat"], tooltip_text="More"))
    return r

_CSS = b"""
label.pill { padding: 1px 8px; border-radius: 99px; background: alpha(currentColor, 0.12); font-weight: bold; }
"""

class Window(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title=TITLE, default_width=880, default_height=660)
        css = Gtk.CssProvider(); css.load_from_data(_CSS)
        Gtk.StyleContext.add_provider_for_display(self.get_display(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.toasts = Adw.ToastOverlay(); self.set_content(self.toasts)
        self.busy = False; self._rows = {}
        tv = Adw.ToolbarView(); self.toasts.set_child(tv)
        self.stack = Adw.ViewStack()
        header = Adw.HeaderBar(); switcher = Adw.ViewSwitcherTitle(stack=self.stack, title=TITLE); header.set_title_widget(switcher)
        m = Gio.Menu(); m.append("Re-run setup / repair", "app.setup"); m.append("Update the app menu", "app.menu"); m.append("About", "app.about")
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=m))
        # Activity indicator: visible on every page while a task runs or a vendor
        # manager is open, so nothing the app does looks like a hang. Click → Progress.
        self._activity = {"task": None, "manager": None, "refresh": None}; self._refreshing = 0
        abox = Gtk.Box(spacing=6); self._activity_spin = Gtk.Spinner(); self._activity_label = Gtk.Label()
        abox.append(self._activity_spin); abox.append(self._activity_label)
        self._activity_btn = Gtk.Button(child=abox, has_frame=False, visible=False, tooltip_text="Show progress")
        self._activity_btn.connect("clicked", lambda *_: self.stack.set_visible_child_name("task" if self._activity["task"] else "install"))
        header.pack_start(self._activity_btn)
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
        elif setup.needs_upgrade_pass():
            # first launch after an update: land the new fixes (patched DLLs,
            # launcher blocks, registry keys) without waiting for a manual repair
            self.run_bg("Applying this update's fixes", lambda r: setup.upgrade_pass(self.prefix, r))
        else: self.refresh_all()
        if os.environ.get("VSTENV_PAGE"): self.stack.set_visible_child_name(os.environ["VSTENV_PAGE"])
        act = os.environ.get("VSTENV_ACTION")   # test hook: run an action at startup
        if act: GLib.timeout_add(500, lambda: ({"sync": self.sync, "setup": self.run_setup}[act](), False)[1])

    # ---- state ------------------------------------------------------------------
    def is_ready(self): return self.prefix is not None and self.prefix.exists and runtime.status(self.prefix)["prepared"]
    def toast(self, text, timeout=4): ui(lambda: self.toasts.add_toast(Adw.Toast(title=text, timeout=timeout)))
    def activity(self, kind, text):
        """Set (or clear with None) what the header indicator says for 'task' or 'manager'."""
        def go():
            self._activity[kind] = text
            cur = self._activity["task"] or self._activity["manager"] or self._activity.get("refresh")
            self._activity_btn.set_visible(bool(cur)); self._activity_spin.set_spinning(bool(cur))
            if cur: self._activity_label.set_text(cur)
        ui(go)

    def _loading_row(self, subtitle="Loading…"):
        r = Adw.ActionRow(title="Loading…", subtitle=subtitle)
        r.add_suffix(Gtk.Spinner(spinning=True, valign=Gtk.Align.CENTER))
        return r

    def loading(self, *groups):
        """Show a spinner row in each group now, and light the header 'Loading…'
        indicator, until the matching refresh replaces the rows. Called on the main
        thread right before a refresh's worker starts."""
        for g in groups:
            self._fill(g, [self._loading_row()])
        self._refreshing = getattr(self, "_refreshing", 0) + 1
        self._refresh_activity()

    def done_loading(self):
        self._refreshing = max(0, getattr(self, "_refreshing", 0) - 1)
        self._refresh_activity()

    def starting(self, name, seconds=8):
        """Show a header 'Starting <name>…' indicator for a few seconds: a Wine app
        takes a moment to draw its first window, and a toast alone is easy to miss."""
        tag = f"Starting {name}…"
        def go():
            self._activity["manager"] = tag; self._refresh_activity_now()
            def clear():
                if self._activity.get("manager") == tag:
                    self._activity["manager"] = None; self._refresh_activity_now()
                return False
            GLib.timeout_add_seconds(seconds, clear)
        ui(go)

    def _refresh_activity_now(self):
        """Recompute the header indicator from the current activity slots. Main thread only."""
        self._activity["refresh"] = "Loading…" if getattr(self, "_refreshing", 0) > 0 else None
        cur = self._activity.get("task") or self._activity.get("manager") or self._activity.get("refresh")
        self._activity_btn.set_visible(bool(cur)); self._activity_spin.set_spinning(bool(cur))
        if cur: self._activity_label.set_text(cur)

    def _refresh_activity(self):
        ui(self._refresh_activity_now)

    def run_bg(self, title, fn, done=None):
        """Run fn(reporter) in a thread on the Progress page."""
        if self.busy: self.toast("Another task is still running"); return
        self.busy = True; self.task.reset(title); self.task_page.set_visible(True); self.stack.set_visible_child_name("task")
        self.task.start(); self.activity("task", f"Working… {title}")
        def worker():
            err = None
            try: result = fn(self.task.reporter)
            except Exception as e:
                err = e; result = None
                for line in traceback.format_exc().rstrip().splitlines(): self.task.reporter.log(line)
            def finish():
                self.busy = False; self.task.stop(); self.activity("task", None)
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
            setup.mark_version_ran()
            self.toast("Environment ready"); self.stack.set_visible_child_name("install")
        self.run_bg("Preparing the environment" if first else "Repairing setup", fn, done)
    def after_change(self, title="Finishing up"):
        self.run_bg(title, lambda r: setup.after_change(self.prefix, r))

    # ---- plugins page ------------------------------------------------------------------
    def build_plugins(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.daw_banner = Adw.Banner(revealed=False); box.append(self.daw_banner)
        bar, self.plugins_search = _search_bar(lambda: self.refresh_plugins(cached=True))
        box.append(bar)
        page = Adw.PreferencesPage(vexpand=True); box.append(page)
        self.compat_group = Adw.PreferencesGroup(title="Compatibility")
        self.compat_row = Adw.ExpanderRow(title="What needed help, and why", subtitle="Nothing installed yet")
        self.compat_group.add(self.compat_row); page.add(self.compat_group); self._compat_rows = []
        self.product_groups = {}
        for v in vendors.all():
            g = Adw.PreferencesGroup(title=f"{v.name} products", description=f"Installed through {v.manager_name}." if v.manager_name else "")
            page.add(g); self.product_groups[v.id] = g
        self.bridged_group = Adw.PreferencesGroup(title="Bridged plugins", description="Available to Linux DAWs through yabridge (~/.vst, ~/.vst3, ~/.clap).")
        page.add(self.bridged_group)
        return box
    def _plugin_filter(self) -> Filter:
        return Filter(self.plugins_search.get_text() if hasattr(self, "plugins_search") else "")

    def _draw_plugins(self, prods, notes, progs, bridged, dc, state):
        """Draw the plugins page from data already fetched. Separate from the
        fetch so the search box can re-draw without touching the prefix."""
        self._plugins_data = dict(prods=prods, notes=notes, progs=progs, bridged=bridged, dc=dc, state=state)
        if state == "missing": self.daw_banner.set_title("Plugins are not bridged: a DAW would run them with the host's wine and damage the prefix — run Re-run setup / repair.")
        self.daw_banner.set_revealed(state != "active")
        for vid, g in self.product_groups.items():
            rows = []
            for x in self._plugin_filter().apply(prods.get(vid, []), lambda y: (y.name, y.kind, y.version)):
                flags = [("registered" if x.registered else "not registered", x.registered)]
                if x.licensed is not None: flags.append(("licensed" if x.licensed else "no license", x.licensed))
                sfx = [Gtk.Label(label=txt, valign=Gtk.Align.CENTER, css_classes=["caption", "dim-label" if ok else "warning"]) for txt, ok in flags]
                note = vendors.note_for(x.name, notes.get(vid, []))
                if note is not None and note.level == "works": note = None      # a pill only where there is something to say
                rows.append(_noted_row(GLib.markup_escape_text(x.name), GLib.markup_escape_text(f"{x.kind or '?'} {x.version}".strip()), note, sfx))
            g.set_visible(bool(rows)); self._fill(g, rows)
        # the compatibility list: every installed product or program with a note that is not plain "works"
        seen, crows, levels = set(), [], []
        items = [(v.id, x.name) for v in vendors.all() for x in prods.get(v.id, [])]
        items += [(v.id, x.name) for x in progs for v in [vendors.for_program(x)] if v is not None]
        found = [(name, vendors.note_for(name, notes.get(vid, []))) for vid, name in items]
        # programs no vendor module claims, drawing through DirectComposition (JUCE 8: Spitfire Audio)
        found += [(x.name, dcomp.note()) for x in progs if vendors.for_program(x) is None and dcomp.is_dcomp_program(x, dc)]
        for name, n in found:
            if n is None or n.level == "works" or name in seen: continue
            seen.add(name); levels.append(n.level)
            r = Adw.ActionRow(title=GLib.markup_escape_text(name), subtitle=GLib.markup_escape_text(n.text), subtitle_lines=0); r.add_suffix(_pill(n.level)); crows.append(r)
        for r in self._compat_rows: self.compat_row.remove(r)
        self._compat_rows = crows
        for r in crows: self.compat_row.add_row(r)
        parts = [f"{levels.count(k)} {vendors.LEVEL_LABELS[k]}" for k in ("patched", "limited", "cannot") if levels.count(k)]
        self.compat_row.set_subtitle(", ".join(parts) if parts else "Everything installed runs as is")
        self.compat_row.set_enable_expansion(bool(crows))
        if not any(prods.values()):
            g = next(iter(self.product_groups.values()), None)
            if g: g.set_visible(True); self._fill(g, [Adw.ActionRow(title="Nothing installed yet", subtitle="Open a vendor's manager from the Install tab")])
        shown = self._plugin_filter().apply(bridged, lambda b: (b.get("name"), b.get("info")))
        brows = [Adw.ActionRow(title=GLib.markup_escape_text(b["name"]), subtitle=GLib.markup_escape_text(b["info"])) for b in shown]
        if not brows:
            brows.append(Adw.ActionRow(title="Nothing matches this search", subtitle="Clear the search box to see everything")
                         if self._plugin_filter().active else
                         Adw.ActionRow(title="No bridged plugins yet", subtitle="Install a product, then Bridge plugins now"))
        self._fill(self.bridged_group, brows)
        self.done_loading()

    def refresh_plugins(self, cached=False):
        """Re-read the prefix and draw. cached=True re-draws from the last fetch,
        which is what the search box needs: no Wine calls per keystroke."""
        if not self.is_ready(): return
        if cached and getattr(self, "_plugins_data", None):
            self._draw_plugins(**self._plugins_data); return
        self.loading(self.bridged_group, *self.product_groups.values())
        def work():
            prods = {v.id: v.products(self.prefix) for v in vendors.all()}
            notes = {}
            for v in vendors.all():
                try: notes[v.id] = v.product_notes(self.prefix)
                except Exception: notes[v.id] = []
            progs = programs.installed(self.prefix)
            bridged = yabridge.bridged(self.prefix)
            state, detail = yabridge.plugin_wine_status(self.prefix)
            try: dc = dcomp.program_exes(self.prefix)
            except Exception: dc = []
            ui(lambda: self._draw_plugins(prods, notes, progs, bridged, dc, state))
        threading.Thread(target=work, daemon=True).start()

    # ---- programs page ------------------------------------------------------------------
    def _program_filter(self) -> Filter:
        return Filter(self.programs_search.get_text() if hasattr(self, "programs_search") else "")

    def build_programs(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        bar, self.programs_search = _search_bar(lambda: self.refresh_programs(cached=True),
                                                "Search by name, version or publisher")
        box.append(bar)
        page = Adw.PreferencesPage(vexpand=True); box.append(page)
        self.programs_group = Adw.PreferencesGroup(title="Installed programs", description="Everything with an installer record or a Start Menu shortcut in the prefix, plus the vendors' managers. Each also appears in your desktop's application menu.")
        page.add(self.programs_group)
        g = Adw.PreferencesGroup(title="Install")
        g.add(_menu_row("Install Windows software…", "Pick any installer (.exe or .msi; unzip a downloaded .zip first). A vendor's manager or product is recognized and installed with its fixes; anything else runs as a plain installer. Same as the Install tab's installer.", "document-open-symbolic",
                        lambda: self.pick_file("Choose an installer (.exe or .msi)", self.install_any, downloads=True),
                        [("Refresh the list and the app menu", lambda: self.run_bg("Updating the app menu", lambda r: menu.sync(self.prefix, r)))]))
        self._rows_limits = _row("What runs here", "Click for the limits of this environment.", "dialog-information-symbolic", lambda: self.toast(programs.LIMITS, 12))
        g.add(self._rows_limits)
        page.add(g); return box
    def _draw_programs(self, progs, notes, dc):
        """Draw the programs page from data already fetched, so the search box
        re-draws without re-reading the prefix."""
        self._programs_data = dict(progs=progs, notes=notes, dc=dc)
        rows = []
        for x in self._program_filter().apply(progs, lambda y: (y.name, y.version, y.publisher)):
            sub = " · ".join(s for s in (x.version, x.publisher) if s) or ("" if x.exe else "no launcher known (uninstall only)")
            sfx = []
            if x.exe:
                b = Gtk.Button(icon_name="media-playback-start-symbolic", valign=Gtk.Align.CENTER, tooltip_text="Run", css_classes=["flat"])
                b.connect("clicked", lambda *_, prog=x: self.run_program(prog)); sfx.append(b)
            if x.uninstall:
                b = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER, tooltip_text="Uninstall", css_classes=["flat"])
                b.connect("clicked", lambda *_, prog=x: self.uninstall_program(prog)); sfx.append(b)
            v = vendors.for_program(x)
            note = vendors.note_for(x.name, notes.get(v.id, [])) if v is not None else None
            if note is None and v is None and dcomp.is_dcomp_program(x, dc): note = dcomp.note()
            if note is not None and note.level == "works": note = None      # a pill only where there is something to say
            row = _noted_row(GLib.markup_escape_text(x.name), GLib.markup_escape_text(sub), note, sfx)
            if x.exe: row.set_tooltip_text(x.exe)
            rows.append(row)
        if not rows:
            rows.append(Adw.ActionRow(title="Nothing matches this search", subtitle="Clear the search box to see everything")
                        if self._program_filter().active else
                        Adw.ActionRow(title="No programs found", subtitle="Install one below, or a vendor's manager from the Install tab"))
        self._fill(self.programs_group, rows)
        # the Install tab's store rows: Open when the store app is installed
        for name, _vend, _url in vendors.STORES:
            prog = next((x for x in progs if name.lower() in x.name.lower() and x.exe), None)
            self._store_prog[name] = prog
            self.store_open[name].set_visible(prog is not None)
            self.store_get[name].set_visible(prog is None)
        self.done_loading()

    def refresh_programs(self, cached=False):
        if not self.is_ready(): return
        if cached and getattr(self, "_programs_data", None):
            self._draw_programs(**self._programs_data); return
        self.loading(self.programs_group)
        def work():
            progs = programs.installed(self.prefix)
            notes = {}
            for v in vendors.all():
                try: notes[v.id] = v.product_notes(self.prefix)
                except Exception: notes[v.id] = []
            try: dc = dcomp.program_exes(self.prefix)
            except Exception: dc = []
            ui(lambda: self._draw_programs(progs, notes, dc))
        threading.Thread(target=work, daemon=True).start()
    def run_program(self, prog):
        v = vendors.for_program(prog)
        if v is not None and v.is_manager_program(prog): return self.open_manager(v)
        try: proc = programs.run(self.prefix, prog); self.toast(f"{prog.name} is starting…"); self.starting(prog.name)
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
        # One funnel: any downloaded installer goes through install_any, which
        # recognizes vendor managers and products and applies their fixes --
        # the user never has to know which kind of installer they picked.
        page = Adw.PreferencesPage(); self.open_rows = {}; self.get_rows = {}
        g = Adw.PreferencesGroup(title="Install")
        g.add(_menu_row("Install Windows software…",
                        "Pick any installer (.exe or .msi; unzip a downloaded .zip first). A vendor's manager or product is recognized and installed with its fixes; anything else runs as a plain installer. Plugins are bridged either way.",
                        "document-open-symbolic",
                        lambda: self.pick_file("Choose an installer (.exe or .msi)", self.install_any, downloads=True),
                        [("Add a plugin folder", self.pick_folder),
                         ("Bridge plugins now", self.sync),
                         ("Finish interrupted installs", self.finish_installs),
                         ("Install the .NET runtime (Wine Mono)", self.install_mono)]))
        page.add(g)
        g = Adw.PreferencesGroup(title="Vendor stores",
                                 description="Each vendor's own app: sign in, then install and update its products there. New plugins are bridged as they appear.")
        for v in vendors.with_manager():
            o = _row(f"Open {v.manager_name}", "Sign in, install or update products.", "go-next-symbolic", lambda v=v: self.open_manager(v))
            o.set_visible(False); self.open_rows[v.id] = o; g.add(o)
            gr = _row(f"Get {v.manager_name} from {v.name}", "Download it, then pick it under “Install Windows software…” above.",
                      "web-browser-symbolic", lambda v=v: self.open_url(v.download_page))
            self.get_rows[v.id] = gr; g.add(gr)
        # store-like apps without a manager module: ordinary programs, same listing
        self.store_open = {}; self.store_get = {}; self._store_prog = {}
        for name, vend, url in vendors.STORES:
            o = _row(f"Open {name}", f"{vend}'s store app.", "go-next-symbolic", lambda n=name: self.open_store(n))
            o.set_visible(False); self.store_open[name] = o; g.add(o)
            gr = _row(f"Get {name} from {vend}", "Download it, then pick its installer under “Install Windows software…” above.",
                      "web-browser-symbolic", lambda u=url: self.open_url(u))
            self.store_get[name] = gr; g.add(gr)
        page.add(g); return page
    def open_store(self, name):
        prog = self._store_prog.get(name)
        if prog is None: self.toast(f"{name} is not installed yet"); return
        self.run_program(prog)
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
    def install_any(self, path):
        """The one install funnel: recognize what the picked installer is and run
        the matching flow with its fixes, saying what was recognized."""
        kind, v = vendors.classify_installer(path)
        if kind == "manager":
            self.toast(f"Recognized the {v.manager_name} installer — installing it with {v.name}'s fixes")
            self.install_manager(v, path)
        elif kind == "product":
            self.toast(f"Recognized a {v.name} product installer")
            self.install_product(v, path)
        else:
            self.install_program(path)
    def open_manager(self, v):
        if not self.is_ready(): self.toast("Run setup first"); return
        if not v.manager_installed(self.prefix): self.toast(f"{v.manager_name} is not installed yet — get it from {v.name} and pick the installer here"); return
        try: proc = v.launch_manager(self.prefix)
        except Exception as e: self.toast(str(e)); return
        self.toast(f"{v.manager_name} is starting…"); self.starting(v.manager_name); t0 = time.time()
        def watch():
            # While the manager runs: rescue an install it drives that stopped making
            # progress, and bridge plugins as they appear (people install a product and
            # leave the manager open, then wonder why the DAW cannot see it).
            w = None; self.activity("manager", f"{v.manager_name} is open")
            try: seen = yabridge.plugin_signature(self.prefix)
            except Exception: seen = None
            while proc.poll() is None:
                try:
                    w, stalled, pids = v.watch_installs(self.prefix, w)
                    self.activity("manager", f"{v.manager_name} is installing… (watched, {w.quiet_for():.0f}s without progress)" if pids and w.quiet_for() >= 30
                                  else f"{v.manager_name} is installing…" if pids else f"{v.manager_name} is open")
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
            proc.wait(); self.activity("manager", None)
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
    def install_mono(self):
        from . import mono
        if not self.is_ready(): self.toast("Run setup first"); return
        self.run_bg("Installing the .NET runtime (Wine Mono)", lambda r: mono.install(self.prefix, r))
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
                    row = self.get_rows.get(vid)
                    if row and n: row.set_subtitle(GLib.markup_escape_text(n))
            ui(show)
        threading.Thread(target=work, daemon=True).start()
    def refresh_install(self):
        """A vendor's row is Open when its manager is installed, Get when not."""
        ready = self.is_ready()
        for v in vendors.with_manager():
            installed = ready and v.manager_installed(self.prefix)
            self.open_rows[v.id].set_visible(installed)
            self.get_rows[v.id].set_visible(not installed)
            ver = (v.manager_version(self.prefix) or "") if installed else ""
            self.open_rows[v.id].set_subtitle(f"{ver} · sign in, install or update products." if ver else "Sign in, install or update products.")
    def refresh_all(self): self.refresh_install(); self.refresh_plugins(); self.refresh_programs(); self.refresh_health(); self.refresh_notices()

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
