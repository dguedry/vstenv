"""GTK4 / libadwaita front end. Thin: every action calls the package."""
import os
import os, sys, threading, time, traceback
from pathlib import Path
import gi
gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk, Gio, Gdk
from . import __version__, APP_ID, APP_NAME, paths, wine, yabridge, doctor, programs, setup, vendors, menu, runtime, dcomp, unmanaged, recipes, webview2
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
    list from a Filter. Returns (widget, entry)."""
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

_RENDERING_LABELS = {"auto": "Auto (detected)", "dxvk": "DXVK", "wine": "Wine's Direct3D", "gdi": "No Direct3D 11"}
_TRI_LABELS = {"auto": "Auto (detected)", "on": "On", "off": "Off"}
_WM_TEXT = {"auto": "Off unless a vendor module asks for it (Komplete Kontrol's menus need it).",
            "on": unmanaged.EXPLAIN.split("\n\n", 1)[-1], "off": "The window manager handles its windows, as for any program."}
_GL_TEXT = {"auto": "Hardware rendering with the full-frame presentation flags, unless the prefix-wide switch in the menu is on.",
            "on": "The embedded browser draws in software. Try it when its window stays blank or black.",
            "off": "Hardware rendering, whatever the prefix-wide switch says."}
_WHO = {"you": "your choice", "recipe": "a shared recipe", "auto": "what the app detected"}

def remedies_label(settings):
    from . import remedies
    return remedies.label(settings)


class TroubleshootDialog(Adw.Dialog):
    """The few settings one program or plugin can be given: try one, keep what
    worked, share it. Thin over recipes.py; the window does the work."""
    def __init__(self, win, kind, key, label, version=None, exe_path=None, is_browser=False):
        super().__init__(title=f"Troubleshoot {label}", content_width=580)
        self.win, self.kind, self.key, self.label, self.version, self.exe_path = win, kind, key, label, version, exe_path
        eff = recipes.effective(win.prefix, kind, key)
        tv = Adw.ToolbarView(); tv.add_top_bar(Adw.HeaderBar())
        page = Adw.PreferencesPage()
        g = Adw.PreferencesGroup(description=recipes.UNNECESSARY + (" Changes apply the next time the program starts."
                                                                     if kind == "programs" else " Changes apply the next time your DAW loads the plugin."))
        self.rows = {}
        self.rows["rendering"] = self._combo(g, "Direct3D", _RENDERING_LABELS, recipes.RENDERING_TEXT, eff["rendering"])
        if kind == "programs":
            self.rows["wm_away"] = self._combo(g, unmanaged.LABEL, _TRI_LABELS, _WM_TEXT, eff["wm_away"])
            if is_browser:
                self.rows["browser_software_gl"] = self._combo(g, "Software rendering for its browser window", _TRI_LABELS, _GL_TEXT,
                                                               eff["browser_software_gl"])
        page.add(g)
        g2 = Adw.PreferencesGroup(title="Share what you found",
                                  description="A setting that fixed something here can help the next person. Share opens a "
                                              "prefilled issue on the project's GitHub in your browser; nothing is sent until "
                                              "you press Submit there.")
        self.symptom = Adw.EntryRow(title="What was wrong, in a sentence"); g2.add(self.symptom)
        share = Adw.ActionRow(title="Share this recipe…", subtitle="With the settings chosen above", activatable=True)
        share.add_suffix(Gtk.Image(icon_name="go-next-symbolic")); share.connect("activated", lambda *_: self.share())
        g2.add(share); page.add(g2)
        tv.set_content(page)
        bar = Gtk.Box(spacing=8, margin_top=6, margin_bottom=12, margin_start=12, margin_end=12, halign=Gtk.Align.END)
        reset = Gtk.Button(label="Reset to detected"); reset.connect("clicked", lambda *_: self.reset()); bar.append(reset)
        apply = Gtk.Button(label="Apply", css_classes=["suggested-action"]); apply.connect("clicked", lambda *_: self.apply()); bar.append(apply)
        tv.add_bottom_bar(bar)
        self.set_child(tv)

    def _combo(self, group, title, labels, texts, current):
        """A row with the choices of one knob; its subtitle explains the one selected."""
        keys = list(labels)
        row = Adw.ComboRow(title=title, model=Gtk.StringList.new([labels[k] for k in keys]))
        value, who = current
        row.set_selected(keys.index(value) if value in keys else 0)
        row.set_use_markup(False)          # the texts quote names; no escaping games
        def explain(*_):
            k = keys[row.get_selected()]
            row.set_subtitle(texts.get(k, "") + (f"  (now: {_WHO[who]})" if k == value else ""))
        row.connect("notify::selected", explain); explain()
        row.vstenv_keys = keys
        group.add(row)
        return row

    def values(self) -> dict:
        return {knob: row.vstenv_keys[row.get_selected()] for knob, row in self.rows.items()}

    def apply(self):
        vals = self.values()
        try: cur = recipes.set_choice(self.win.prefix, self.kind, self.key, **vals)
        except ValueError as e: self.win.toast(str(e)); return
        self.close(); self.win.apply_recipe(self.kind, self.key, self.label, cur, self.exe_path)

    def reset(self):
        recipes.reset(self.win.prefix, self.kind, self.key)
        self.close(); self.win.apply_recipe(self.kind, self.key, self.label, {}, self.exe_path)

    def share(self):
        url = recipes.share_url(self.win.prefix, self.kind, self.key, self.values(), self.symptom.get_text().strip(), self.version)
        Gtk.UriLauncher(uri=url).launch(self.win, None, None)


class InstallFailedDialog(Adw.Dialog):
    """A failed install ends here: what the failure says, the remedies worth
    trying (the fitting ones first), and a way to send the failure in."""
    def __init__(self, win, path, err):
        super().__init__(title=f"{Path(path).name} did not install", content_width=600)
        self.win, self.path, self.err = win, Path(path), err
        tv = Adw.ToolbarView(); tv.add_top_bar(Adw.HeaderBar())
        page = Adw.PreferencesPage()
        from . import remedies
        tech = getattr(err, "tech", "unknown"); code = getattr(err, "exit_code", None)
        what = [f"Exit code {code}" if code is not None else "It did not finish",
                f"installer technology: {remedies.TECH_LABEL.get(tech, tech)}"]
        g = Adw.PreferencesGroup(title="What happened", description=" · ".join(what))
        for line in getattr(err, "diagnosis", []) or ["Nothing in its output that the app recognises. The remedies below are the usual suspects, in order."]:
            g.add(Adw.ActionRow(title=GLib.markup_escape_text(line), title_lines=0))
        page.add(g)
        g2 = Adw.PreferencesGroup(title="Try one", description="Each runs the installer again with one change. Pick the one whose description matches what you saw.")
        for rid, lab, when, settings, suggested in remedies.options(tech, code, getattr(err, "log_tail", lambda: "")()):
            row = Adw.ActionRow(title=lab, subtitle=when, subtitle_lines=0, activatable=True)
            if suggested: row.add_suffix(_pill("patched")); row.set_title(lab + "  (fits this failure)")
            row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
            row.connect("activated", lambda *_, s=settings: self.retry(s))
            g2.add(row)
        page.add(g2)
        g3 = Adw.PreferencesGroup(title="Send it in", description="Opens a prefilled issue on the project's GitHub with the exit code, the "
                                                                    "installer's output (your user name removed) and this machine's facts; "
                                                                    "nothing is sent until you press Submit there.")
        self.symptom = Adw.EntryRow(title="What you saw, in a sentence"); g3.add(self.symptom)
        share = Adw.ActionRow(title="Share this failure…", activatable=True); share.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
        share.connect("activated", lambda *_: self.share()); g3.add(share)
        log = getattr(err, "log", None)
        if log: g3.add(Adw.ActionRow(title="Its output was kept", subtitle=GLib.markup_escape_text(str(log)), subtitle_lines=0))
        page.add(g3)
        tv.set_content(page); self.set_child(tv)

    def retry(self, settings):
        self.close(); self.win.install_program(self.path, remedy=settings)

    def share(self):
        from . import report
        url = recipes.share_url(self.win.prefix, "installs", self.path.name, getattr(self.err, "remedy", {}) or {},
                                self.symptom.get_text().strip(), worked=False,
                                extra=report.scrub(getattr(self.err, "log_tail", lambda: "")()), diagnosis=getattr(self.err, "diagnosis", []))
        Gtk.UriLauncher(uri=url).launch(self.win, None, None)


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
        self.busy = False; self._busy_title = None; self._rows = {}
        tv = Adw.ToolbarView(); self.toasts.set_child(tv)
        self.stack = Adw.ViewStack()
        header = Adw.HeaderBar(); switcher = Adw.ViewSwitcherTitle(stack=self.stack, title=TITLE); header.set_title_widget(switcher)
        from . import webview2
        m = Gio.Menu(); m.append("Re-run setup / repair", "app.setup"); m.append("Update the app menu", "app.menu")
        m.append(webview2.SOFTWARE_GL_LABEL, "app.softgl"); m.append("About", "app.about")
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
        # Tab icons ship with the app (vstenv/icons/README.md): a desktop theme could
        # replace a stock name with one that draws nothing, or lack it altogether.
        Gtk.IconTheme.get_for_display(self.get_display()).add_search_path(str(Path(__file__).parent / "icons"))
        self.stack.add_titled_with_icon(self.build_plugins(), "plugins", "Plugins", "vstenv-tab-plugins-symbolic")
        self.stack.add_titled_with_icon(self.build_programs(), "programs", "Programs", "vstenv-tab-programs-symbolic")
        self.stack.add_titled_with_icon(self.build_install(), "install", "Install", "vstenv-tab-install-symbolic")
        self.stack.add_titled_with_icon(self.build_health(), "health", "Health", "vstenv-tab-health-symbolic")
        self.task = TaskPage("Working")
        self.task_page = self.stack.add_titled_with_icon(self.task, "task", "Progress", "vstenv-tab-progress-symbolic")
        self.task_page.set_visible(False)
        self._stale = set()
        self.stack.connect("notify::visible-child-name", self._refresh_stale_page)
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
        takes a moment to draw its first window, and a toast alone is easy to miss.
        started() ends it early, when the window is seen."""
        tag = f"Starting {name}…"
        def go():
            self._activity["manager"] = tag; self._refresh_activity_now()
            GLib.timeout_add_seconds(seconds, lambda: (self.started(name), False)[1])
        ui(go)

    def started(self, name):
        """Drop the 'Starting <name>…' indicator (main thread only)."""
        if self._activity.get("manager") == f"Starting {name}…":
            self._activity["manager"] = None; self._refresh_activity_now()

    def wait_cursor_until_window(self, exe_names, name):
        """A wait cursor over this window from the click until the launched
        program's first window maps (or it exits, or 30 s pass); the header's
        'Starting…' ends at the same moment. Watching happens off the main thread."""
        from . import launchwait, wedge
        exe_names = tuple(x for x in exe_names if x)
        if not exe_names: return
        self.set_cursor(Gdk.Cursor.new_from_name("wait"))
        def pids(): return {pid for x in exe_names for pid, _ in self.prefix.processes(x)}
        def work():
            try: launchwait.wait_for_window(pids, wedge.windowed_pids)   # programs.run's watcher does the resync
            except Exception: pass
            ui(lambda: (self.set_cursor(None), self.started(name)))
        threading.Thread(target=work, daemon=True).start()

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
        if self.busy:
            # Name what is running and offer the page, so the refusal is not a dead end.
            t = Adw.Toast(title=f"Busy: {self._busy_title or 'a task is running'}", timeout=5,
                          button_label="Show")
            t.connect("button-clicked", lambda *_: self.stack.set_visible_child_name("task"))
            ui(lambda: self.toasts.add_toast(t)); return
        self.busy = True; self._busy_title = title; self.task.reset(title); self.task_page.set_visible(True); self.stack.set_visible_child_name("task")
        self.task.start(); self.activity("task", f"Working… {title}")
        def worker():
            err = None
            try: result = fn(self.task.reporter)
            except Exception as e:
                err = e; result = None
                for line in traceback.format_exc().rstrip().splitlines(): self.task.reporter.log(line)
            def finish():
                self.busy = False; self._busy_title = None; self.task.stop(); self.activity("task", None)
                if err:
                    # Our own errors already say what happened and what to do; only an
                    # unexpected exception needs the type spelled out.
                    known = isinstance(err, (RuntimeError, OSError)) and str(err)
                    self.task.reporter.step(str(err) if known else f"{type(err).__name__}: {err}")
                    self.task.reporter.fail()
                if done: done(result, err)
                self.refresh_all()
                if err is None: GLib.timeout_add_seconds(3, self._hide_task_page)
            ui(finish)
        threading.Thread(target=worker, daemon=True).start()
    def _hide_task_page(self):
        """Leave a finished task's page where the user can get back to it.

        It used to be hidden a few seconds after success, which threw away the
        only record of what happened: the toast is gone by the time anyone
        wonders. Move off the page so it is not in the way, but keep the tab,
        so "what did that actually do?" is still answerable until the next task
        replaces it."""
        if not self.busy and self.stack.get_visible_child_name() == "task": self.stack.set_visible_child_name("plugins")
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
        def brow(b):
            r = Adw.ActionRow(title=GLib.markup_escape_text(b["name"]), subtitle=GLib.markup_escape_text(b["info"]))
            t = Gtk.Button(icon_name="preferences-system-symbolic", valign=Gtk.Align.CENTER, css_classes=["flat"],
                           tooltip_text=f"Troubleshoot… — {recipes.UNNECESSARY}")
            t.connect("clicked", lambda *_, name=b["name"]: self.troubleshoot_plugin(name)); r.add_suffix(t)
            return r
        brows = [brow(b) for b in shown]
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
        self.programs_group = Adw.PreferencesGroup(title="Installed programs", description="Everything with an installer record or a Start Menu shortcut in the prefix, plus the vendors' managers. Each also appears in your desktop's application menu. Drivers and other background components are grouped at the end.")
        page.add(self.programs_group)
        g = Adw.PreferencesGroup(title="Install")
        g.add(_menu_row("Install Windows software…", "Pick any installer (.exe or .msi; unzip a downloaded .zip first). A vendor's manager or product is recognized and installed with its fixes; anything else runs as a plain installer. Same as the Install tab's installer.", "document-open-symbolic",
                        lambda: self.pick_file("Choose an installer (.exe or .msi)", self.install_any, downloads=True),
                        [("Refresh the list and the app menu", lambda: self.run_bg("Updating the app menu", lambda r: menu.sync(self.prefix, r)))]))
        self._rows_limits = _row("What runs here", "Click for the limits of this environment.", "dialog-information-symbolic", lambda: self.toast(programs.LIMITS, 12))
        g.add(self._rows_limits)
        page.add(g); return box
    def _draw_programs(self, progs, notes, dc, um=(), hosts=()):
        """Draw the programs page from data already fetched, so the search box
        re-draws without re-reading the prefix. `um` holds the exe paths that are
        kept away from the window manager and `hosts` the exe names whose window
        is an embedded browser (both read once, off the UI thread)."""
        self._programs_data = dict(progs=progs, notes=notes, dc=dc, um=um, hosts=hosts)
        def build_row(x):
            sub = " · ".join(s for s in (x.version, x.publisher) if s) or ("" if x.exe else "no launcher known (uninstall only)")
            um_on = x.exe in um
            if um_on: sub = " · ".join(s for s in (sub, "window manager kept away") if s)
            sfx = []
            if x.exe:
                b = Gtk.Button(icon_name="media-playback-start-symbolic", valign=Gtk.Align.CENTER, tooltip_text="Run", css_classes=["flat"])
                b.connect("clicked", lambda *_, prog=x: self.run_program(prog)); sfx.append(b)
            if x.uninstall:
                b = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER, tooltip_text="Uninstall", css_classes=["flat"])
                b.connect("clicked", lambda *_, prog=x: self.uninstall_program(prog)); sfx.append(b)
            if unmanaged.supported(x):
                b = Gtk.Button(icon_name="preferences-system-symbolic", valign=Gtk.Align.CENTER,     # crossed wrench and screwdriver
                               tooltip_text=f"Troubleshoot… — {recipes.UNNECESSARY}", css_classes=["flat"])
                b.connect("clicked", lambda *_, prog=x: self.troubleshoot_program(prog, hosts)); sfx.append(b)
            v = vendors.for_program(x)
            note = vendors.note_for(x.name, notes.get(v.id, [])) if v is not None else None
            if note is None and v is None and dcomp.is_dcomp_program(x, dc): note = dcomp.note()
            if note is not None and note.level == "works": note = None      # a pill only where there is something to say
            row = _noted_row(GLib.markup_escape_text(x.name), GLib.markup_escape_text(sub), note, sfx)
            if x.exe: row.set_tooltip_text(x.exe)
            return row

        matched = self._program_filter().apply(progs, lambda y: (y.name, y.version, y.publisher))
        # Drivers, runtimes and licence services are not what anyone came to the
        # page for, and they are the rows where a stray uninstall does the most
        # damage. Keep them, one expander down.
        front = [x for x in matched if not x.background]
        back = [x for x in matched if x.background]
        rows = [build_row(x) for x in front]
        if not rows and not back:
            rows.append(Adw.ActionRow(title="Nothing matches this search", subtitle="Clear the search box to see everything")
                        if self._program_filter().active else
                        Adw.ActionRow(title="No programs found", subtitle="Install one below, or a vendor's manager from the Install tab"))
        if back:
            exp = Adw.ExpanderRow(title=f"Background components ({len(back)})",
                                  subtitle="Drivers, runtimes and licence services other programs rely on. Nothing to open; uninstall only if you know you want it gone.")
            for x in back: exp.add_row(build_row(x))
            rows.append(exp)
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
            # one hive read for every program's switch, not a registry query each
            try: on = unmanaged.applied_all(self.prefix)
            except Exception: on = set()
            um = {x.exe for x in progs if unmanaged.supported(x) and unmanaged.exe_name(x) in on}
            try: hosts = set(webview2.host_exes(self.prefix))      # cached scan: a stat per exe
            except Exception: hosts = set()
            ui(lambda: self._draw_programs(progs, notes, dc, um, hosts))
        threading.Thread(target=work, daemon=True).start()
    def run_program(self, prog):
        v = vendors.for_program(prog)
        if v is not None and v.is_manager_program(prog): return self.open_manager(v)
        try: proc = self.with_startup_id(prog.name, lambda: programs.run(self.prefix, prog)); self.toast(f"{prog.name} is starting…"); self.starting(prog.name, seconds=30)
        except Exception as e: self.toast(str(e)); return
        exe = (prog.exe or "").rsplit("\\", 1)[-1]
        self.wait_cursor_until_window((exe,), prog.name)
        self._guard_launch((exe,), prog.name, lambda: programs.run(self.prefix, prog))
        # The one moment the user is looking at the window: if this is a
        # WebView2 app and it comes up blank, say what to do. Nothing watches
        # the window (that needs a display and would misfire headless), so
        # this is offered, not triggered -- a user staring at a white window
        # has no reason to go and open Health.
        self._webview2_hint(prog)
        def watch():           # a program may install plugins while it runs: bridge them as they appear, and at exit
            programs.watch_plugins(self.prefix, proc, lambda: ui(lambda: self.after_change(f"Bridging new plugins from {prog.name}")))
            ui(lambda: self.after_change(f"Bridging plugins after {prog.name}"))
        threading.Thread(target=watch, daemon=True).start()
    def set_software_gl(self, action, on):
        """The menu checkbox: switch software GL for WebView2 apps and rewrite
        their flags now, so the next launch has them -- no setup run needed."""
        from . import webview2
        if self.prefix is None: self.toast("Run setup first"); return
        def work(r):
            done = webview2.set_software_gl(self.prefix, on, r)
            ui(lambda: action.set_state(GLib.Variant.new_boolean(on)))
            if done: self.toast(webview2.restart_note(done), 8)
        self.run_bg(f"{webview2.SOFTWARE_GL_LABEL}: {'on' if on else 'off'}", work)

    def startup_id(self, name: str) -> str | None:
        """A startup-notification id stamped with the X time of the user's last
        input to this GUI -- the click that launches the program. Wine puts
        that time on the new window, and the window manager then focuses it
        only if the user has not moved on since (see Prefix.wine_env)."""
        try:
            gi.require_version("GdkX11", "4.0")
            from gi.repository import GdkX11
            disp = Gdk.Display.get_default()
            if not isinstance(disp, GdkX11.X11Display): return None
            t = disp.get_user_time()
        except Exception: return None
        if not t: return None
        slug = "".join(c if c.isalnum() else "-" for c in name)[:32]
        return f"vstenv-{os.getpid()}-{slug}_TIME{t}"

    def with_startup_id(self, name, launch):
        """Run `launch()` with DESKTOP_STARTUP_ID set for it, then restore."""
        sid = self.startup_id(name); prev = os.environ.get("DESKTOP_STARTUP_ID")
        if sid: os.environ["DESKTOP_STARTUP_ID"] = sid
        try: return launch()
        finally:
            if sid:
                if prev is None: os.environ.pop("DESKTOP_STARTUP_ID", None)
                else: os.environ["DESKTOP_STARTUP_ID"] = prev

    def _guard_launch(self, exe_names, name, relaunch):
        """A launch that wedges the Wine session (nothing new gets a window) is
        healed by closing what was just launched and launching it again; do
        that in the background and say so. See launchguard."""
        from . import launchguard
        exe_names = tuple(x for x in exe_names if x)
        if not exe_names: return
        def work():
            try: result = launchguard.guard(self.prefix, exe_names, relaunch)
            except Exception: return
            msg = launchguard.message(result, name)
            if msg: self.toast(msg, 10)
            if result == "recovered": ui(self.refresh_health)
        threading.Thread(target=work, daemon=True).start()

    def _webview2_hint(self, prog):
        """Tell a user whose WebView2 window may come up blank what to try.

        Shown once per session, a few seconds after launch so it lands while
        the window is on screen, and only when the software-GL flag is not
        already on."""
        from . import webview2
        if webview2.software_gl(self.prefix): return
        if getattr(self, "_wv2_hinted", False): return
        exe = (prog.exe or "").rsplit("\\", 1)[-1]
        try:
            if exe not in set(webview2.host_exes(self.prefix)): return
        except Exception:
            return
        self._wv2_hinted = True
        GLib.timeout_add_seconds(12, lambda: (self.toast(
            f"If its window stays blank, close it and switch on '{webview2.SOFTWARE_GL_LABEL}' "
            "in the \u2630 menu, then open it again.", 12), False)[1])

    def uninstall_program(self, prog):
        """Confirm first: the trash icon sits next to Run, and a mis-click here
        removes software the user paid for."""
        v = vendors.for_program(prog)
        extra = ""
        if v is not None and v.is_manager_program(prog):
            extra = (f"\n\n{prog.name} is {v.name}'s manager: without it you cannot install, update or "
                     f"authorise that vendor's products from here until you install it again.")
        d = Adw.AlertDialog(heading=f"Uninstall {prog.name}?",
                            body=f"This runs the program's own uninstaller in the prefix. Anything it has "
                                 f"downloaded or authorised may go with it, and reinstalling may mean "
                                 f"downloading and signing in again.{extra}")
        d.add_response("cancel", "Cancel")
        d.add_response("uninstall", "Uninstall")
        d.set_response_appearance("uninstall", Adw.ResponseAppearance.DESTRUCTIVE)
        d.set_default_response("cancel"); d.set_close_response("cancel")
        d.connect("response", lambda _d, resp: self._do_uninstall(prog) if resp == "uninstall" else None)
        d.present(self)

    def _do_uninstall(self, prog):
        self.run_bg(f"Uninstalling {prog.name}", lambda r: (programs.uninstall(self.prefix, prog, r), setup.after_change(self.prefix, r)))
    def troubleshoot_program(self, prog, hosts=()):
        """The settings this program can be given (recipes.py), in a dialog."""
        exe = unmanaged.exe_name(prog)
        TroubleshootDialog(self, "programs", exe, prog.name, version=prog.version,
                           exe_path=self.prefix.to_host(prog.exe), is_browser=exe in set(hosts)).present(self)

    def troubleshoot_plugin(self, name):
        TroubleshootDialog(self, "plugins", name, name).present(self)

    def apply_recipe(self, kind, key, label, settings, exe_path=None):
        """Write a dialog's choice to the prefix. A program's settings are a few
        registry values; a plugin's go through the host launcher's override
        file, which the after-install pass rewrites."""
        said = ", ".join(f"{k}={v}" for k, v in settings.items()) or "back to what the app detected"
        if kind == "programs":
            def work():
                try: recipes.apply_program(self.prefix, key, settings, None, exe_path)
                except Exception as e: self.toast(f"Could not change {label}: {str(e)[:80]}"); return
                self.toast(f"{label}: {said} — restart it to see the change", 6)
                ui(lambda: self.refresh_programs())
            threading.Thread(target=work, daemon=True).start()
        else:
            def done(_r, err):
                if not err: self.toast(f"{label}: {said} — reload it in your DAW to see the change", 6)
            self.run_bg(f"Applying the recipe for {label}", lambda r: setup.after_change(self.prefix, r, force=True), done)
    def install_program(self, path, remedy=None):
        """Run an installer; with `remedy`, run it again the way a remedy says.
        A failure opens the remedies dialog; a success after a remedy keeps it
        for this file and offers to share it."""
        def work(r):
            # A tuple here would evaluate both halves: a failed install would
            # still run the environment pass, spending a minute bridging and
            # syncing menus for something that was not installed.
            programs.install(self.prefix, path, r, remedy=remedy)
            setup.after_change(self.prefix, r)
        def done(_result, err):
            if isinstance(err, programs.InstallCancelled):
                self.toast(f"{Path(path).name} was cancelled — nothing was changed")
            elif isinstance(err, programs.InstallFailed):
                self.toast(f"{Path(path).name} failed — the environment was left alone")
                ui(lambda: InstallFailedDialog(self, path, err).present(self))
            elif not err and remedy:
                self.remedy_worked(path, remedy)
        self.run_bg(f"Installing {Path(path).name}" + (f" ({remedies_label(remedy)})" if remedy else ""), work, done)

    def remedy_worked(self, path, remedy):
        """Keep the remedy for this installer file, and ask about sharing it."""
        name = Path(path).name
        try: recipes.set_choice(self.prefix, "installs", name, **remedy)
        except Exception: pass
        d = Adw.AlertDialog(heading=f"{name} installed",
                            body=f"It worked with: {remedies_label(remedy)}. That is kept for this file, so installing it again "
                                 "just works.\n\nShare it? That opens a prefilled issue on the project's GitHub in your browser; once "
                                 "merged, everyone's app applies it to this installer by itself. Nothing is sent until you press Submit there.")
        d.add_response("later", "Not now"); d.add_response("share", "Share this fix…")
        d.set_response_appearance("share", Adw.ResponseAppearance.SUGGESTED); d.set_default_response("share"); d.set_close_response("later")
        def resp(_d, r):
            if r == "share":
                Gtk.UriLauncher(uri=recipes.share_url(self.prefix, "installs", name, remedy, "", worked=True)).launch(self, None, None)
        d.connect("response", resp); d.present(self)

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
                         ("Rescue a stuck install", self.rescue_installs),
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
    def install_opened(self, path: Path):
        """Install a file the desktop handed us (a double-clicked .exe/.msi).

        The prefix may not exist yet on a first run, and setup may still be
        going, so this waits for the app to be ready instead of failing with
        "run setup first" at the moment someone just asked for an install."""
        if not path.exists():
            self.toast(f"{path.name} is gone"); return
        if path.suffix.lower() not in (".exe", ".msi"):
            self.toast(f"{path.name} is not a Windows installer"); return
        self.stack.set_visible_child_name("install")

        def go():
            if self.is_ready() and not self.busy:
                self.toast(f"Installing {path.name}")
                self.install_any(path)          # a Path, as the file picker passes
                return False          # done, stop polling
            if not self.is_ready() and not self.busy:
                # Nothing is running and we are still not ready: setup has not
                # been done yet, and install_any would only fail.
                self.run_setup()
            return True               # keep waiting
        if go(): GLib.timeout_add_seconds(2, go)

    def install_any(self, path):
        """The one install funnel: recognize what the picked installer is and run
        the matching flow with its fixes, saying what was recognized.

        Takes a path as str or Path; the flows below want a Path."""
        path = Path(path)
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
        try: proc = self.with_startup_id(v.manager_name, lambda: v.launch_manager(self.prefix))
        except Exception as e: self.toast(str(e)); return
        from . import launchwait
        launchwait.watch_and_resync(self.prefix, v.manager_exe_names())
        self.toast(f"{v.manager_name} is starting…"); self.starting(v.manager_name, seconds=30); t0 = time.time()
        self.wait_cursor_until_window(v.manager_exe_names(), v.manager_name)
        self._guard_launch(v.manager_exe_names(), v.manager_name, lambda: v.launch_manager(self.prefix))
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
                        def rescued(res, err):
                            if err or not res: return
                            self.toast("Finished " + ", ".join(x.get("name", "?") for x in res) + " from its staged files; the manager may still say it failed", 10)
                        ui(lambda: self.run_bg("An installer stopped responding — finishing it",
                                               lambda r: (lambda res: (setup.after_change(self.prefix, r), res)[1])(v.rescue_installs(self.prefix, r)),
                                               rescued))
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
    def rescue_installs(self):
        """Stop an installer that has stopped making progress and finish it from
        what it already downloaded.

        Different from "Finish interrupted installs", which completes an
        install whose payload is already unpacked and whose installer is gone.
        This one is for a setup still sitting there doing nothing -- a vendor's
        manager drives it, and may stay open throughout: the rescue kills the
        setup and finishes from what it staged whether or not the manager runs."""
        def fn(r):
            res = []
            for v in vendors.all(): res += v.rescue_installs(self.prefix, r)
            if not res:
                r.step("Stuck installs")
                r.skip("none found — an installer still downloading or genuinely working is left alone")
            else:
                setup.after_change(self.prefix, r)
            return res
        def done(res, err):
            if err: return
            if res: self.toast(f"Rescued {len(res)} install(s)")
            else: self.toast("No stuck installer found")
        self.run_bg("Rescuing a stuck install", fn, done)

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
    # Reading a page costs real time in the prefix (the plugin list ~6s, the
    # program list ~3s), so refreshing all five after every task spent ten
    # seconds re-reading pages nobody was looking at. Refresh what is on screen
    # and mark the rest stale; _refresh_stale_page catches them up on the way in.
    _PAGE_REFRESH = {"install": "refresh_install", "plugins": "refresh_plugins",
                     "programs": "refresh_programs", "health": "refresh_health"}

    def refresh_all(self):
        self.refresh_notices()                     # cheap, and drives the header
        current = self.stack.get_visible_child_name()
        self._stale = set(self._PAGE_REFRESH) - {current}
        fn = self._PAGE_REFRESH.get(current)
        if fn: getattr(self, fn)()

    def _refresh_stale_page(self, *_):
        """Catch a page up the first time it is shown after something changed."""
        name = self.stack.get_visible_child_name()
        if name in getattr(self, "_stale", set()):
            self._stale.discard(name)
            fn = self._PAGE_REFRESH.get(name)
            if fn: getattr(self, fn)()

class App(Adw.Application):
    def __init__(self):
        # HANDLES_OPEN: the desktop file offers this app as a handler for .exe
        # and .msi, so a double-clicked installer arrives here as a file to open
        # rather than being ignored.
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_OPEN)
        # A checkbox in the menu: on some stacks a WebView2 window never paints
        # without software GL (see webview2.SOFTWARE_GL_FLAGS). Setting, not
        # setup -- flipping it rewrites the apps' flags right away.
        from . import webview2
        b = wine.installed_build()      # no window yet: read the setting from the prefix on disk
        sg = Gio.SimpleAction.new_stateful("softgl", None, GLib.Variant.new_boolean(
            webview2.software_gl(wine.Prefix(paths.PREFIX, b) if b else None)))
        sg.connect("change-state", lambda act, val: self.win.set_software_gl(act, val.get_boolean())); self.add_action(sg)
        for name, cb in (("setup", lambda *_: self.win.run_setup()), ("about", self.about),
                         ("menu", lambda *_: self.win.run_bg("Updating the app menu", lambda r: menu.sync(self.win.prefix, r)))):
            act = Gio.SimpleAction(name=name); act.connect("activate", cb); self.add_action(act)
        for i, page in enumerate(("plugins", "programs", "install", "health"), start=1):
            act = Gio.SimpleAction(name=f"tab{i}"); act.connect("activate", lambda *_, p=page: self.win.stack.set_visible_child_name(p))
            self.add_action(act); self.set_accels_for_action(f"app.tab{i}", [f"<Control>{i}"])
    def do_activate(self):
        """One window. The app is single-instance, so a launcher click while it
        runs lands here as a second activation: raise the window that exists
        rather than open another next to it."""
        if not getattr(self, "win", None) or self.win not in self.get_windows():
            self.win = Window(self)
        self.win.present()

    def do_open(self, files, n_files, hint):
        """A Windows installer was opened with this app (double-clicked, or
        'Open With'). Present the window, then run it through the same funnel
        the Install tab uses, so it gets the vendor's fixes rather than a bare
        Wine run."""
        self.do_activate()
        paths_ = [f.get_path() for f in files if f.get_path()]
        if not paths_:
            self.win.toast("That file is not on this machine — copy it here first")
            return
        if len(paths_) > 1:
            self.win.toast(f"Opening the first of {len(paths_)} files")
        self.win.install_opened(Path(paths_[0]))
    def about(self, *_):
        Adw.AboutWindow(transient_for=self.win, application_name=TITLE, version=__version__,
                        comments="A managed environment for Windows audio plugins on Linux: vendor managers, their products, and VST bridging — without touching Wine yourself.").present()

def main():
    paths.ensure_dirs()
    from . import runtimedir
    try: runtimedir.heal()          # left 0755 by older vstenv Flatpaks; distro Wine refuses it
    except Exception: pass
    sys.exit(App().run(sys.argv))

if __name__ == "__main__": main()
