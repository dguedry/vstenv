import unittest
from pathlib import Path
from vstenv.wine import Prefix, WineBuild

class Scope(unittest.TestCase):
    def make(self, running, procs):
        p = Prefix(Path("/nonexistent/prefix"), WineBuild(Path("/nonexistent/wine")))
        p.wineserver_running = lambda: running
        p.processes = lambda exe_name=None: procs
        return p
    def test_none(self): self.assertEqual(self.make(False, []).wineserver_scope(), "none")
    def test_ours(self): self.assertEqual(self.make(True, [(1, "/x/bin/wineserver"), (2, "C:\\a.exe")]).wineserver_scope(), "ours")
    def test_foreign(self): self.assertEqual(self.make(True, []).wineserver_scope(), "foreign")
    def test_foreign_with_only_clients_visible(self):
        # our own clients can be visible while the server that serves them is not
        self.assertEqual(self.make(True, [(2, "C:\\windows\\system32\\cmd.exe")]).wineserver_scope(), "foreign")

if __name__ == "__main__": unittest.main()
