"""Which wine runs a bridged plugin: the prefix records it (<prefix>/wineloader)
and yabridge's host launcher, which the app installs, honours the record. Every
other prefix, and an explicit WINELOADER, behave exactly as upstream."""
import os, stat, subprocess, tempfile, unittest
from pathlib import Path
from unittest import mock
from vstenv import yabridge
from vstenv.wine import Prefix, WineBuild

# yabridge-host.exe exactly as winegcc generates it (yabridge 5.1.1 and master)
UPSTREAM_LAUNCHER = '''#!/bin/sh

appname="yabridge-host.exe.so"
# determine the application directory
appdir=''
case "$0" in
  */*)
    # $0 contains a path, use it
    appdir=`dirname "$0"`
    ;;
  *)
    # no directory in $0, search in PATH
    saved_ifs=$IFS
    IFS=:
    for d in $PATH
    do
      IFS=$saved_ifs
      if [ -x "$d/$appname" ]; then appdir="$d"; break; fi
    done
    ;;
esac

# figure out the full app path
if [ -n "$appdir" ]; then
    apppath="$appdir/$appname"
    WINEDLLPATH="$appdir:$WINEDLLPATH"
    export WINEDLLPATH
else
    apppath="$appname"
fi

# determine the WINELOADER
if [ ! -x "$WINELOADER" ]; then WINELOADER="wine"; fi

# and try to start the app
exec "$WINELOADER" "$apppath" "$@"
'''

def _exe(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text); path.chmod(path.stat().st_mode | stat.S_IXUSR)

def _fake_wine(path: Path, name: str):
    _exe(path, f'#!/bin/sh\necho "{name} prefix=$WINEPREFIX fsync=${{WINEFSYNC-unset}} args=$*"\n')

class PluginWineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); root = Path(self.tmp.name)
        self.home = root / "home"; (self.home / ".wine").mkdir(parents=True)
        build = root / "wine-build"; _fake_wine(build / "bin/wine", "app wine")
        self.prefix = Prefix(root / "prefix", WineBuild(build)); (self.prefix.path / "drive_c").mkdir(parents=True)
        self.hostbin = root / "usr-bin"; _fake_wine(self.hostbin / "wine", "host wine")
        self.yab = root / "share/yabridge"
        self.launcher = self.yab / "yabridge-host.exe"; _exe(self.launcher, UPSTREAM_LAUNCHER)
        self.patches = [mock.patch.object(yabridge, "YAB_DIR", self.yab),
                        mock.patch.object(yabridge, "YCTL", self.yab / "yabridgectl"),
                        mock.patch.object(yabridge.Path, "home", staticmethod(lambda: self.home))]
        for p in self.patches: p.start()

    def tearDown(self):
        for p in self.patches: p.stop()
        self.tmp.cleanup()

    def activate(self):
        self.assertTrue(self.prefix.declare_wine())
        self.assertTrue(yabridge.ensure_host_launchers())
        self.assertEqual(yabridge.plugin_wine_status(self.prefix)[0], "active")

    def run_host(self, wineprefix=None, **extra):
        """Start the launcher the way libyabridge does: WINEPREFIX already set to the
        prefix it detected from the plugin's location, the DAW's PATH otherwise."""
        env = {"HOME": str(self.home), "PATH": str(self.hostbin), **extra}
        if wineprefix is not None: env["WINEPREFIX"] = str(wineprefix)
        cp = subprocess.run([str(self.launcher), "--version"], env=env, capture_output=True, text=True, timeout=30)
        return cp.returncode, cp.stdout.strip(), cp.stderr.strip()

    # ---- routing --------------------------------------------------------------------------
    def test_app_prefix_runs_app_wine(self):
        self.activate()
        rc, out, err = self.run_host(self.prefix.path)
        self.assertEqual(rc, 0, err); self.assertTrue(out.startswith("app wine"), out)
        self.assertIn("fsync=1", out); self.assertIn("yabridge-host.exe.so --version", out)

    def test_other_prefix_is_untouched(self):
        self.activate()
        other = self.home / "games-prefix"; other.mkdir()
        rc, out, _ = self.run_host(other)
        self.assertEqual(rc, 0); self.assertTrue(out.startswith("host wine"), out)
        self.assertIn("fsync=unset", out, "the record must not leak WINEFSYNC into other prefixes")

    def test_unset_prefix_is_the_users_own(self):
        self.activate()
        rc, out, _ = self.run_host(None)
        self.assertTrue(out.startswith("host wine"), out)

    def test_explicit_wineloader_still_wins(self):
        self.activate()
        mine = self.home / "mine/wine"; _fake_wine(mine, "my wine")
        rc, out, _ = self.run_host(self.prefix.path, WINELOADER=str(mine))
        self.assertTrue(out.startswith("my wine"), out)

    def test_app_wine_gone_falls_back_to_host_wine(self):
        self.activate()
        os.remove(self.prefix.build.root / "bin/wine")
        rc, out, _ = self.run_host(self.prefix.path)
        self.assertEqual(rc, 0); self.assertTrue(out.startswith("host wine"), out)

    def test_upstream_launcher_unchanged_without_the_record(self):
        # not activated: upstream behaviour, byte for byte
        rc, out, _ = self.run_host(self.prefix.path)
        self.assertTrue(out.startswith("host wine"), out); self.assertIn("fsync=unset", out)

    # ---- the launcher patch -----------------------------------------------------------------
    def test_patch_is_idempotent_and_keeps_the_mode(self):
        self.assertEqual(yabridge.patch_host_launcher(self.launcher), "patched")
        once = self.launcher.read_text()
        self.assertEqual(yabridge.patch_host_launcher(self.launcher), "already")
        self.assertEqual(self.launcher.read_text(), once)
        self.assertEqual(once.count("\nexec "), 1); self.assertIn(yabridge.LAUNCHER_MARK, once)
        self.assertTrue(os.access(self.launcher, os.X_OK))

    def test_unrecognised_launcher_is_left_alone(self):
        self.launcher.write_text("#!/bin/sh\nexec wine something\n")
        self.assertEqual(yabridge.patch_host_launcher(self.launcher), "unrecognised")
        self.assertEqual(self.launcher.read_text(), "#!/bin/sh\nexec wine something\n")
        self.assertFalse(yabridge.ensure_host_launchers())
        self.prefix.declare_wine()
        st, detail = yabridge.plugin_wine_status(self.prefix)
        self.assertEqual(st, "missing"); self.assertIn("host's wine", detail)

    def test_no_launcher_installed(self):
        self.launcher.unlink()
        self.assertFalse(yabridge.ensure_host_launchers())
        self.prefix.declare_wine()
        self.assertEqual(yabridge.plugin_wine_status(self.prefix), ("missing", "yabridge is not installed"))

    # ---- the prefix record ------------------------------------------------------------------
    def test_declare_wine_records_and_updates(self):
        self.assertTrue(self.prefix.declare_wine())
        self.assertEqual(self.prefix.wineloader_file.read_text(), f"{self.prefix.build.wine}\n")
        self.assertFalse(self.prefix.declare_wine(), "idempotent")
        newer = self.prefix.path.parent / "wine-newer"; _fake_wine(newer / "bin/wine", "newer")
        moved = Prefix(self.prefix.path, WineBuild(newer))
        self.assertTrue(moved.declare_wine())
        self.assertEqual(moved.wineloader_file.read_text(), f"{newer / 'bin/wine'}\n")

    def test_status_needs_both_record_and_launcher(self):
        st, detail = yabridge.plugin_wine_status(self.prefix)
        self.assertEqual(st, "missing"); self.assertIn("does not record", detail)
        self.prefix.declare_wine()
        st, detail = yabridge.plugin_wine_status(self.prefix)
        self.assertEqual(st, "missing"); self.assertIn("yabridge-host.exe", detail)
        yabridge.ensure_host_launchers()
        st, detail = yabridge.plugin_wine_status(self.prefix)
        self.assertEqual(st, "active"); self.assertIn("other prefixes keep", detail)

if __name__ == "__main__":
    unittest.main()
