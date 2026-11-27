"""ntsync: probe the host, offer once, enable through pkexec on the host."""
import tempfile, unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from vstenv import ntsync


def _probe(**kw):
    d = dict(device=0, usable=0, module=1, boot=0, pkexec=1, used=0); d.update(kw)
    return "\n".join(f"{k}={v}" for k, v in d.items()) + "\n"


class NtsyncTest(unittest.TestCase):
    def test_summary_and_offer(self):
        with mock.patch("vstenv.host.sh", return_value=_probe()):
            s = ntsync.status()
            self.assertFalse(ntsync.summary(s)[0]); self.assertTrue(ntsync.can_enable(s))
        with mock.patch("vstenv.host.sh", return_value=_probe(device=1, usable=1, boot=1, used=1)):
            self.assertEqual(ntsync.summary(ntsync.status())[0], True)
            self.assertFalse(ntsync.can_enable(ntsync.status()))
        with mock.patch("vstenv.host.sh", return_value=_probe(module=0)):
            ok, why = ntsync.summary(ntsync.status()); self.assertTrue(ok); self.assertIn("no ntsync driver", why)

    def test_a_no_is_remembered(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(ntsync, "DECLINED", Path(tmp) / "d"), \
             mock.patch("vstenv.host.sh", return_value=_probe()):
            self.assertTrue(ntsync.should_offer())
            ntsync.decline()
            self.assertFalse(ntsync.should_offer())

    def test_enable_asks_pkexec_on_the_host(self):
        probes = iter([_probe(), _probe(device=1, usable=1, boot=1)])
        with mock.patch("vstenv.host.sh", side_effect=lambda *a, **k: next(probes)), \
             mock.patch("vstenv.host.run", return_value=SimpleNamespace(returncode=0, stderr="")) as run:
            ok, msg = ntsync.enable()
        self.assertTrue(ok); self.assertIn("Close your DAW", msg)
        self.assertEqual(run.call_args.args[0][:3], ["pkexec", "sh", "-c"])
        self.assertIn("modprobe ntsync", run.call_args.args[0][3])

    def test_a_cancelled_prompt_changes_nothing(self):
        with mock.patch("vstenv.host.sh", return_value=_probe()), \
             mock.patch("vstenv.host.run", return_value=SimpleNamespace(returncode=126, stderr="")):
            ok, msg = ntsync.enable()
        self.assertFalse(ok); self.assertIn("cancelled", msg)

    def test_without_pkexec_it_says_what_to_run(self):
        with mock.patch("vstenv.host.sh", return_value=_probe(pkexec=0)):
            ok, msg = ntsync.enable()
        self.assertFalse(ok); self.assertIn("modprobe ntsync", msg)


if __name__ == "__main__": unittest.main()


class NofileTests(unittest.TestCase):
    def _status(self, probe):
        with mock.patch.object(ntsync.host, "sh", return_value=probe): return ntsync.status()

    def test_a_low_hard_limit_is_flagged_only_while_ntsync_is_usable(self):
        self.assertTrue(ntsync.nofile_low(self._status("device=1\nusable=1\nnofile=4096\n")))
        self.assertFalse(ntsync.nofile_low(self._status("device=0\nusable=0\nnofile=4096\n")))

    def test_a_high_or_unlimited_limit_is_fine(self):
        self.assertFalse(ntsync.nofile_low(self._status("usable=1\nnofile=524288\n")))
        s = self._status("usable=1\nnofile=unlimited\n")
        self.assertIsNone(s["nofile"]); self.assertFalse(ntsync.nofile_low(s))
