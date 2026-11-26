"""WineASIO's settings window: what the driver's Control Panel button opens.

The driver runs `wineasio-settings` on the host (wineasio.LAUNCHER), which starts
`vstenv wineasio settings`: this window, on its own, for this app's prefix. It is not
part of the main window's application (NON_UNIQUE, no bus name), so it opens beside a
running vstenv and from inside the program whose Control Panel button was pressed.
"""
import threading
import gi
gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk, Gio
from . import wineasio

class SettingsWindow(Adw.ApplicationWindow):
    def __init__(self, app, prefix):
        super().__init__(application=app, title="WineASIO settings", default_width=460, default_height=680)
        self.prefix = prefix; self.rows = {}
        tv = Adw.ToolbarView(); tv.add_top_bar(Adw.HeaderBar()); self.set_content(tv)
        self.toasts = Adw.ToastOverlay(); tv.set_content(self.toasts)
        page = Adw.PreferencesPage(); self.toasts.set_child(page)
        on = wineasio.enabled(prefix)
        g = Adw.PreferencesGroup(title="Channels", description=None if on else
                                 "WineASIO is off. Turn it on with: vstenv wineasio enable")
        page.add(g)
        for name, title in (("inputs", "Inputs"), ("outputs", "Outputs")):
            _, _, lo, hi, _ = wineasio.SETTINGS[name]
            row = Adw.SpinRow.new_with_range(lo, hi, 1); row.set_title(title); g.add(row); self.rows[name] = row
        g = Adw.PreferencesGroup(title="Buffer", description="Smaller buffers mean less delay and more load on the computer.")
        page.add(g)
        row = Adw.SwitchRow(title="Let the program change the buffer size",
                            subtitle="Off: the size is the one PipeWire or JACK already runs at"); g.add(row); self.rows["fixed-buffer"] = row
        size = Adw.ComboRow(title="Buffer size to offer", subtitle="Samples",
                            model=Gtk.StringList.new([str(n) for n in wineasio.BUFFER_SIZES])); g.add(size); self.rows["buffer"] = size
        row.bind_property("active", size, "sensitive", 2)       # 2 = SYNC_CREATE: the driver ignores it while the size is fixed
        g = Adw.PreferencesGroup(title="Connections"); page.add(g)
        row = Adw.SwitchRow(title="Connect to the sound card", subtitle="When the driver starts"); g.add(row); self.rows["connect-hardware"] = row
        row = Adw.SwitchRow(title="Start a JACK server when none runs", subtitle="Not needed with PipeWire"); g.add(row); self.rows["autostart-jack"] = row
        g = Adw.PreferencesGroup(description="Changes apply the next time a program opens the driver: "
                                 "choose another ASIO driver and WineASIO again, or restart the program.")
        page.add(g)
        self.apply_button = Gtk.Button(label="Apply", css_classes=["suggested-action", "pill"], halign=Gtk.Align.CENTER, sensitive=False)
        self.apply_button.connect("clicked", lambda *_: self.apply()); g.add(self.apply_button)
        threading.Thread(target=self._load, daemon=True).start()      # reading the registry starts Wine: not on the UI thread

    def _load(self):
        vals = wineasio.settings(self.prefix); GLib.idle_add(self._show, vals)

    def _show(self, vals):
        self.loaded = dict(vals)
        self.rows["inputs"].set_value(vals["inputs"]); self.rows["outputs"].set_value(vals["outputs"])
        self.rows["buffer"].set_selected(wineasio.BUFFER_SIZES.index(vals["buffer"]) if vals["buffer"] in wineasio.BUFFER_SIZES
                                         else wineasio.BUFFER_SIZES.index(1024))
        self.rows["fixed-buffer"].set_active(not vals["fixed-buffer"])     # the switch reads the other way round
        self.rows["connect-hardware"].set_active(bool(vals["connect-hardware"]))
        self.rows["autostart-jack"].set_active(bool(vals["autostart-jack"]))
        self.apply_button.set_sensitive(True)

    def values(self) -> dict:
        r = self.rows
        return {"inputs": int(r["inputs"].get_value()), "outputs": int(r["outputs"].get_value()),
                "buffer": wineasio.BUFFER_SIZES[r["buffer"].get_selected()],
                "fixed-buffer": 0 if r["fixed-buffer"].get_active() else 1,
                "connect-hardware": int(r["connect-hardware"].get_active()), "autostart-jack": int(r["autostart-jack"].get_active())}

    def apply(self):
        want = {k: v for k, v in self.values().items() if self.loaded.get(k) != v}
        if not want: self.toasts.add_toast(Adw.Toast(title="Nothing to change")); return
        self.apply_button.set_sensitive(False)
        def work():
            try:
                for k, v in want.items(): wineasio.set_setting(self.prefix, k, v)
                msg = "Saved. Reopen the driver in the program to use it."; self.loaded.update(want)
            except Exception as e: msg = f"Could not save: {e}"
            GLib.idle_add(lambda: (self.toasts.add_toast(Adw.Toast(title=msg)), self.apply_button.set_sensitive(True)) and False)
        threading.Thread(target=work, daemon=True).start()

def run(prefix) -> int:
    app = Adw.Application(application_id=None, flags=Gio.ApplicationFlags.NON_UNIQUE)
    app.connect("activate", lambda a: SettingsWindow(a, prefix).present())
    return app.run([])
