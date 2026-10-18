import unittest

from vstenv import launchwait


class WaitForWindowTest(unittest.TestCase):
    def _run(self, pids_seq, windowed_seq, timeout=30.0):
        t = [0.0]
        pids = list(pids_seq); win = list(windowed_seq)
        def pids_of(): return pids.pop(0) if len(pids) > 1 else pids[0]
        def windowed(): return win.pop(0) if len(win) > 1 else win[0]
        def sleep(s): t[0] += s
        return launchwait.wait_for_window(pids_of, windowed, timeout=timeout, sleep=sleep, now=lambda: t[0])

    def test_returns_when_the_program_owns_a_window(self):
        self.assertEqual(self._run([{10, 11}], [set(), set(), {11, 99}]), "window")

    def test_returns_exited_once_the_processes_are_gone_after_the_grace(self):
        # pids present at first (spawned), then gone: it quit before drawing
        self.assertEqual(self._run([{10}, {10}, {10}, {10}, {10}, {10}, set()], [set()]), "exited")

    def test_no_pids_at_the_very_start_is_not_exited_yet(self):
        """The spawn takes a moment; an empty scan at t=0 means nothing."""
        self.assertEqual(self._run([set(), {10}, {10}], [set(), set(), {10}]), "window")

    def test_times_out(self):
        self.assertEqual(self._run([{10}], [set()], timeout=5.0), "timeout")

    def test_unreadable_window_list_gives_up_at_once(self):
        """No wmctrl / no display: nothing to wait on, do not hold the cursor."""
        self.assertEqual(self._run([{10}], [None]), "unknown")

    def test_gui_waits_on_both_launch_paths(self):
        from pathlib import Path
        src = Path("vstenv/gui.py").read_text()
        self.assertEqual(src.count("self.wait_cursor_until_window("), 2)
        self.assertIn('Gdk.Cursor.new_from_name("wait")', src)


class ResyncTest(unittest.TestCase):
    """Gig Performer 5, 2026-10-18: Cinnamon maximized the window as it appeared,
    Wine kept its own placement, pointer events landed 258 px off until the
    maximized state was toggled."""

    def test_maximized_window_is_toggled(self):
        calls = []
        def sh(cmd):
            calls.append(cmd)
            return "_NET_WM_STATE(ATOM) = _NET_WM_STATE_MAXIMIZED_HORZ, _NET_WM_STATE_MAXIMIZED_VERT" if cmd.startswith("xprop") else ""
        self.assertTrue(launchwait.resync_if_maximized("0x0b200010", sh))
        self.assertEqual(len(calls), 2)
        self.assertIn("remove,maximized_vert,maximized_horz", calls[1]); self.assertIn("add,maximized_vert,maximized_horz", calls[1])

    def test_ordinary_window_is_left_alone(self):
        calls = []
        def sh(cmd): calls.append(cmd); return "_NET_WM_STATE(ATOM) = " if cmd.startswith("xprop") else ""
        self.assertFalse(launchwait.resync_if_maximized("0x1", sh))
        self.assertEqual(len(calls), 1, "no wmctrl call for a window that is not maximized")

    def test_every_launch_path_gets_the_watcher(self):
        """programs.run covers the GUI's program launches, `vstenv run` and the
        desktop-menu entries; manager launches in the GUI and CLI attach it too."""
        from pathlib import Path
        self.assertEqual(Path("vstenv/programs.py").read_text().count("launchwait.watch_and_resync("), 2)
        self.assertIn("launchwait.watch_and_resync(self.prefix, v.manager_exe_names())", Path("vstenv/gui.py").read_text())
        cli = Path("vstenv/cli.py").read_text()
        self.assertIn("launchwait.watch_and_resync(p, v.manager_exe_names())", cli)
        self.assertEqual(cli.count("_join_window_watch(proc)"), 2, "run and manager launch both stay for the window")

    def test_watch_and_resync_toggles_once_the_window_is_up(self):
        import types
        from unittest import mock
        calls = []
        p = types.SimpleNamespace(processes=lambda exe: [(42, exe)])
        with mock.patch.object(launchwait, "resync_if_maximized", side_effect=lambda w: calls.append(w) or True), \
             mock.patch.object(launchwait.time, "sleep", lambda s: None):
            t = launchwait.watch_and_resync(p, ("GigPerformer5.exe",), windowed=lambda: {42}, windows_of=lambda pids: ["0x0b200010"])
            t.join(5)
        self.assertEqual(calls, ["0x0b200010"])

    def test_watch_and_resync_does_nothing_without_a_window(self):
        import types
        from unittest import mock
        calls = []
        p = types.SimpleNamespace(processes=lambda exe: [])     # exited at once
        with mock.patch.object(launchwait, "resync_if_maximized", side_effect=lambda w: calls.append(w)), \
             mock.patch.object(launchwait, "GRACE", 0.0), mock.patch.object(launchwait.time, "sleep", lambda s: None):
            launchwait.watch_and_resync(p, ("x.exe",), windowed=lambda: set(), windows_of=lambda pids: ["0x1"]).join(5)
        self.assertEqual(calls, [])


class WindowsOfTest(unittest.TestCase):
    def test_ids_for_pids(self):
        from unittest import mock
        from vstenv import wedge
        out = "0x0b200010  0 1824038 host Gig Performer\n0x03c00039  0 20531 host Code\n"
        with mock.patch.object(wedge.host, "sh", return_value=out):
            self.assertEqual(wedge.windows_of({1824038}), ["0x0b200010"])
            self.assertEqual(wedge.windows_of({1}), [])
