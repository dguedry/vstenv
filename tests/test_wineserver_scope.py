import unittest
from pathlib import Path
from unittest import mock
from vstenv import host
from vstenv.wine import Prefix, WineBuild

class Scope(unittest.TestCase):
    def make(self, running, procs, holders=[]):
        p = Prefix(Path("/nonexistent/prefix"), WineBuild(Path("/nonexistent/wine")))
        p.wineserver_running = lambda: running
        p.processes = lambda exe_name=None: procs
        p.lock_holders = lambda: holders
        return p
    def test_none(self): self.assertEqual(self.make(False, []).wineserver_scope(), "none")
    def test_ours(self): self.assertEqual(self.make(True, [(1, "/x/bin/wineserver"), (2, "C:\\a.exe")]).wineserver_scope(), "ours")
    def test_foreign(self): self.assertEqual(self.make(True, []).wineserver_scope(), "foreign")
    def test_foreign_with_only_clients_visible(self):
        # our own clients can be visible while the server that serves them is not
        self.assertEqual(self.make(True, [(2, "C:\\windows\\system32\\cmd.exe")]).wineserver_scope(), "foreign")
    def test_lock_holder_wins_over_environment_scan(self):
        # a wineserver started with an unusual environment is still ours if the host sees its pid
        self.assertEqual(self.make(True, [], holders=[4242]).wineserver_scope(), "ours")
    def test_unreadable_locks_and_empty_scan_is_unknown_not_foreign(self):
        self.assertEqual(self.make(True, [], holders=None).wineserver_scope(), "unknown")
    def test_unreadable_locks_but_server_in_scan_is_ours(self):
        self.assertEqual(self.make(True, [(1, "/x/bin/wineserver")], holders=None).wineserver_scope(), "ours")

class LockHolders(unittest.TestCase):
    def make(self, tmp):
        p = Prefix(Path(tmp), WineBuild(Path("/nonexistent/wine")))
        d = Path(tmp) / "server"; d.mkdir(); (d / "lock").write_bytes(b"")
        p.wineserver_dir = lambda: d
        return p, d / "lock"
    def test_parses_holder_of_our_lock(self):
        import os, tempfile
        with tempfile.TemporaryDirectory() as tmp:
            p, lock = self.make(tmp); st = lock.stat()
            key = f"{os.major(st.st_dev):02x}:{os.minor(st.st_dev):02x}:{st.st_ino}"
            locks = (f"1: POSIX  ADVISORY  WRITE 111 {key} 0 EOF\n"
                     f"1: -> POSIX  ADVISORY  WRITE 222 {key} 0 EOF\n"      # a waiter, not a holder
                     f"2: POSIX  ADVISORY  WRITE 0 {key} 0 EOF\n"           # holder in another pid namespace
                     f"3: FLOCK  ADVISORY  WRITE 333 {key[:-1]}9 0 EOF\n")  # some other file
            with mock.patch.object(host, "sh", lambda script, **kw: locks + "__locks_read__\n"):
                self.assertEqual(p.lock_holders(), [111])
    def test_unreadable_proc_locks_is_none(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            p, _ = self.make(tmp)
            with mock.patch.object(host, "sh", lambda script, **kw: ""):
                self.assertIsNone(p.lock_holders())
    def test_no_lock_file_means_no_holders(self):
        p = Prefix(Path("/nonexistent/prefix"), WineBuild(Path("/nonexistent/wine")))
        self.assertEqual(p.lock_holders(), [])

if __name__ == "__main__": unittest.main()
