import unittest
from unittest import mock
from vstenv import yabridge


class SystemYabridgeTest(unittest.TestCase):
    def test_lists_what_the_host_has(self):
        with mock.patch("vstenv.host.sh", return_value="/usr/lib/libyabridge-vst3.so\n/usr/bin/yabridge-host.exe\n") as sh:
            self.assertEqual(yabridge.system_installs(), ["/usr/lib/libyabridge-vst3.so", "/usr/bin/yabridge-host.exe"])
        self.assertIn("/usr/lib/libyabridge*.so", sh.call_args.args[0])

    def test_none(self):
        with mock.patch("vstenv.host.sh", return_value=""):
            self.assertEqual(yabridge.system_installs(), [])


if __name__ == "__main__": unittest.main()
