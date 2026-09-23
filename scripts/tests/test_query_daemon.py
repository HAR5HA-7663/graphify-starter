import os
import unittest
from unittest import mock

from scripts import config, query_client


class QueryClientTest(unittest.TestCase):
    def test_no_socket_means_in_process(self):
        with mock.patch.object(config, "QUERY_SOCKET", config.BRAIN_ROOT / ".no-such.sock"):
            self.assertIsNone(query_client.ask({"question": "x"}))

    def test_disabled_skips_daemon(self):
        with mock.patch.object(config, "QUERY_DAEMON_ENABLED", False):
            self.assertIsNone(query_client.ask({"question": "x"}))

    def test_fingerprint_tracks_behaviour_env(self):
        base = query_client.fingerprint()
        with mock.patch.dict(os.environ, {"BRAIN_PRIVACY_STRICT": "1"}):
            self.assertNotEqual(base, query_client.fingerprint())
        self.assertEqual(base, query_client.fingerprint())


if __name__ == "__main__":
    unittest.main()
