import unittest
from unittest import mock

from vstenv import programs


class Proc:
    def __init__(self, alive_for): self.n = alive_for
    def poll(self):
        self.n -= 1
        return None if self.n >= 0 else 0


class WatchPluginsTest(unittest.TestCase):
    """Softube Heartbeat, 2026-10-30: installed by Softube Central's own daemon
    while Central stayed open, and never bridged until it closed."""

    def _run(self, sigs, alive_for):
        calls = []; seq = list(sigs)
        def sig(_p): return seq.pop(0) if len(seq) > 1 else seq[0]
        with mock.patch("vstenv.yabridge.plugin_signature", side_effect=sig):
            programs.watch_plugins(object(), Proc(alive_for), lambda: calls.append(1), sleep=lambda s: None)
        return len(calls)

    def test_bridges_once_the_new_files_hold_still(self):
        a, b = frozenset({"x"}), frozenset({"x", "y"})
        self.assertEqual(self._run([a, a, b, b, b, b], alive_for=6), 1)

    def test_a_still_growing_install_waits(self):
        a, b, c = frozenset({"x"}), frozenset({"x", "y"}), frozenset({"x", "y", "z"})
        self.assertEqual(self._run([a, b, c, c, c], alive_for=4), 1, "bridged once, after it settled")

    def test_nothing_new_means_no_bridge(self):
        a = frozenset({"x"})
        self.assertEqual(self._run([a] * 6, alive_for=5), 0)

    def test_gui_and_cli_both_watch(self):
        from pathlib import Path
        self.assertIn("programs.watch_plugins(self.prefix, proc", Path("vstenv/gui.py").read_text())
        self.assertIn("programs.watch_plugins(p, proc", Path("vstenv/cli.py").read_text())
