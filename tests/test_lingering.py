import subprocess
import time
import unittest
from unittest import mock

from vstenv import lingering


class PipeOfTest(unittest.TestCase):
    def test_names_a_real_pipe(self):
        p = subprocess.Popen(["sh", "-c", "echo hi"], stdout=subprocess.PIPE, text=True)
        try:
            self.assertRegex(lingering.pipe_of(p.stdout) or "", r"^pipe:\[\d+\]$")
        finally:
            p.stdout.close(); p.wait()

    def test_none_when_there_is_no_pipe(self):
        self.assertIsNone(lingering.pipe_of(None))
        with open("/dev/null") as f:
            self.assertNotEqual(lingering.pipe_of(f), "")   # a file, not pipe:[...]


class HoldersTest(unittest.TestCase):
    def test_empty_pipe_name_finds_nothing(self):
        self.assertEqual(lingering.holders(""), [])

    def test_our_own_shells_and_interpreter_are_not_reported(self):
        """Only processes worth naming to a person come back: the shell the
        command ran through is not the vendor daemon we are looking for."""
        scan = "123\tpython3\n124\tsh\n125\tflatpak-spawn\n126\tNTKDaemon.exe\n"
        with mock.patch.object(lingering.host, "sh", return_value=scan):
            self.assertEqual(lingering.holders("pipe:[1]"), [(126, "NTKDaemon.exe")])

    def test_excluded_pids_are_dropped(self):
        scan = "200\tNTKDaemon.exe\n201\tservices.exe\n"
        with mock.patch.object(lingering.host, "sh", return_value=scan):
            self.assertEqual(lingering.holders("pipe:[1]", exclude={200}), [(201, "services.exe")])

    def test_garbage_lines_are_ignored(self):
        with mock.patch.object(lingering.host, "sh", return_value="not-a-pid\tx\n\n"):
            self.assertEqual(lingering.holders("pipe:[1]"), [])


class DescribeTest(unittest.TestCase):
    def test_nothing_found_says_nothing(self):
        self.assertEqual(lingering.describe([]), "")

    def test_names_are_listed_once(self):
        text = lingering.describe([(1, "NTKDaemon.exe"), (2, "NTKDaemon.exe"), (3, "services.exe")])
        self.assertIn("NTKDaemon.exe", text)
        self.assertIn("services.exe", text)
        self.assertEqual(text.count("NTKDaemon.exe"), 1)


class RunWatchingTest(unittest.TestCase):
    """The case this exists for: a command exits while something it started
    keeps its stdout open, so reading to EOF waits for that process instead."""

    def test_a_clean_command_returns_at_once_with_its_output(self):
        start = time.monotonic()
        cp = lingering.run_watching(["sh", "-c", "echo one; echo two"], grace=3.0)
        self.assertEqual(cp.returncode, 0)
        self.assertEqual(cp.stdout.split(), ["one", "two"])
        self.assertLess(time.monotonic() - start, 3.0, "a clean command must not wait out the grace")

    def test_a_lingering_child_is_named_and_does_not_block_forever(self):
        seen = {}
        start = time.monotonic()
        cp = lingering.run_watching(
            ["sh", "-c", "(sleep 30 &) ; echo started"],
            grace=2.0,
            on_linger=lambda found, note: seen.update(found=found, note=note))
        waited = time.monotonic() - start
        self.assertEqual(cp.stdout.strip(), "started", "output written before the linger is kept")
        self.assertIn("found", seen, "the holder should have been reported")
        self.assertTrue(any("sleep" in name for _pid, name in seen["found"]))
        self.assertLess(waited, 25.0, "must not wait for the lingering child to exit")

    def test_on_linger_is_optional(self):
        cp = lingering.run_watching(["sh", "-c", "(sleep 5 &) ; echo ok"], grace=1.0)
        self.assertEqual(cp.stdout.strip(), "ok")


class EndTest(unittest.TestCase):
    def test_nothing_to_end_runs_no_command(self):
        with mock.patch.object(lingering.host, "sh") as sh:
            self.assertEqual(lingering.end([]), [])
            sh.assert_not_called()

    def test_signals_each_pid(self):
        with mock.patch.object(lingering.host, "sh") as sh:
            self.assertEqual(lingering.end([(11, "a"), (12, "b")]), [11, 12])
            self.assertIn("11 12", sh.call_args[0][0])


if __name__ == "__main__":
    unittest.main()


class ExeOfTest(unittest.TestCase):
    """Windows command lines are backslash-separated and full of spaces."""

    def test_unquoted_path_with_spaces(self):
        from vstenv.wine import _exe_of
        self.assertEqual(
            _exe_of(r"C:\Program Files\Audio Modeling\SWAM Violin\SWAM Violin 3.exe"),
            "SWAM Violin 3.exe")

    def test_quoted_path_with_arguments(self):
        from vstenv.wine import _exe_of
        self.assertEqual(_exe_of(r'"C:\Program Files\NI\NTKDaemon.exe" --service'), "NTKDaemon.exe")

    def test_arguments_after_an_unquoted_path(self):
        from vstenv.wine import _exe_of
        self.assertEqual(_exe_of(r"C:\x\setup.exe /S --quiet"), "setup.exe")

    def test_empty(self):
        from vstenv.wine import _exe_of
        self.assertEqual(_exe_of(""), "")
        self.assertEqual(_exe_of("   "), "")


class LingeringNoteTest(unittest.TestCase):
    """Wine's own services always run, so naming them would bury the vendor
    daemon that is actually holding the output open."""

    def _prefix(self, procs):
        from vstenv.wine import Prefix
        p = Prefix.__new__(Prefix)
        p.processes = lambda exe_name=None: procs
        return p

    def test_only_wine_services_running_says_nothing(self):
        p = self._prefix([(1, r"C:\windows\system32\services.exe"),
                          (2, r"C:\windows\system32\explorer.exe"),
                          (3, r"C:\windows\system32\rpcss.exe")])
        self.assertEqual(p.lingering_note(), "")

    def test_a_vendor_daemon_is_named(self):
        p = self._prefix([(1, r"C:\windows\system32\services.exe"),
                          (2, r"C:\Program Files\NI\NTKDaemon.exe")])
        note = p.lingering_note()
        self.assertIn("NTKDaemon.exe", note)
        self.assertNotIn("services.exe", note)

    def test_nothing_running_says_nothing(self):
        self.assertEqual(self._prefix([]).lingering_note(), "")

    def test_a_broken_process_list_is_not_fatal(self):
        from vstenv.wine import Prefix
        p = Prefix.__new__(Prefix)
        def boom(exe_name=None): raise OSError("no /proc")
        p.processes = boom
        self.assertEqual(p.lingering_note(), "")


class RunWatchingContractTest(unittest.TestCase):
    """Prefix.run() now goes through run_watching, so it has to behave exactly
    as host.run() did for the 40-odd callers that use it."""

    def test_stdout_and_stderr_stay_separate(self):
        """reg_add reads cp.stderr; merging the streams would break it."""
        cp = lingering.run_watching(["sh", "-c", "echo out; echo err >&2"], timeout=30)
        self.assertEqual(cp.stdout.strip(), "out")
        self.assertEqual(cp.stderr.strip(), "err")

    def test_returncode_is_passed_through(self):
        cp = lingering.run_watching(["sh", "-c", "exit 3"], timeout=30)
        self.assertEqual(cp.returncode, 3)

    def test_a_command_that_overruns_raises_timeout_expired(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            lingering.run_watching(["sh", "-c", "sleep 20"], timeout=2)

    def test_capture_false_skips_the_machinery(self):
        """An installer run with capture=False has no pipes to linger on."""
        cp = lingering.run_watching(["sh", "-c", "exit 0"], timeout=30, capture=False)
        self.assertEqual(cp.returncode, 0)

    def test_one_scan_covers_both_pipes(self):
        """The /proc walk costs seconds, so stdout and stderr are looked up in
        a single pass rather than one each."""
        calls = []
        def fake_sh(script, env=None, timeout=60):
            calls.append(env.get("VSTENV_PIPES", ""))
            return ""
        with mock.patch.object(lingering.host, "sh", fake_sh):
            lingering.holders(["pipe:[1]", "pipe:[2]"])
        self.assertEqual(len(calls), 1)
        self.assertIn("pipe:[1]", calls[0])
        self.assertIn("pipe:[2]", calls[0])

    def test_a_pid_holding_both_pipes_is_reported_once(self):
        with mock.patch.object(lingering.host, "sh",
                               return_value="42\tNTKDaemon.exe\n42\tNTKDaemon.exe\n"):
            self.assertEqual(lingering.holders(["pipe:[1]", "pipe:[2]"]), [(42, "NTKDaemon.exe")])
