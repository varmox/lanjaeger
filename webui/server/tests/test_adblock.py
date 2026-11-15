"""Ad-blocking tests against a fake Blocky API."""

import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from lanjaeger_webui import adblock


class FakeBlocky(BaseHTTPRequestHandler):
    state = {"enabled": True, "disabledGroups": []}
    calls: list[str] = []

    def log_message(self, *a):
        pass

    def _send(self, data):
        body = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        FakeBlocky.calls.append(self.path)
        if self.path.startswith("/api/blocking/disable"):
            FakeBlocky.state = {"enabled": True, "disabledGroups": ["ads"]}
        elif self.path == "/api/blocking/enable":
            FakeBlocky.state = {"enabled": True, "disabledGroups": []}
        elif self.path == "/api/stats":
            return self._send({"summary": {"queries": 100, "blocked": 12}, "perHour": [],
                               "topBlockedDomains": [{"name": "ads.example", "count": 9}]})
        self._send(FakeBlocky.state)

    def do_POST(self):
        FakeBlocky.calls.append(self.path)
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()


class AdblockTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeBlocky)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        adblock.BLOCKY_URL = f"http://127.0.0.1:{cls.srv.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        (self.dir / "blocky").mkdir()
        (self.dir / "blocky" / "allowlist.txt").write_text("# keep this header\n")
        (self.dir / ".env").write_text("TZ=UTC\n")
        FakeBlocky.calls = []
        FakeBlocky.state = {"enabled": True, "disabledGroups": []}

    def test_normalize(self):
        good, bad = adblock.normalize(["Ads.Example.com.", "https://track.example.org/x?y", "*.doubleclick.net",
                                       "ads.example.com", "not a domain", "localhost", "xn--bcher-kva.example"])
        self.assertEqual(good, ["*.doubleclick.net", "ads.example.com", "track.example.org", "xn--bcher-kva.example"])
        self.assertEqual(bad, ["not a domain", "localhost"])
        with self.assertRaises(ValueError):
            adblock.normalize("ads.example.com")

    def test_write_list_keeps_inode_and_header(self):
        path = self.dir / "blocky" / "allowlist.txt"
        inode = os.stat(path).st_ino
        adblock.write_list(str(self.dir), "allow", ["a.example", "b.example"])
        adblock.write_list(str(self.dir), "allow", ["c.example"])
        self.assertEqual(os.stat(path).st_ino, inode)  # bind mount still sees it
        self.assertEqual(path.read_text(), "# keep this header\nc.example\n")
        self.assertEqual(adblock.read_list(str(self.dir), "allow"), ["c.example"])

    def test_toggle_persists_and_only_touches_ads_group(self):
        env = str(self.dir / ".env")
        self.assertTrue(adblock.desired_enabled(env))
        adblock.set_desired(env, False)
        adblock.disable()
        self.assertFalse(adblock.desired_enabled(env))
        self.assertIn("/api/blocking/disable?groups=ads", FakeBlocky.calls)
        self.assertFalse(adblock.ads_active(adblock.status()))

    def test_reconciler_reapplies_off_after_restart(self):
        env = str(self.dir / ".env")
        adblock.set_desired(env, False)
        FakeBlocky.state = {"enabled": True, "disabledGroups": []}   # blocky restarted
        r = adblock.Reconciler(env)
        self.assertTrue(r.step())                       # switched off again
        self.assertFalse(adblock.ads_active(adblock.status()))
        self.assertFalse(r.step())                      # already off: no call
        adblock.set_desired(env, True)
        FakeBlocky.state = {"enabled": True, "disabledGroups": []}
        self.assertFalse(r.step())                      # wanted on: leaves it alone

    def test_pause_url(self):
        adblock.disable(30)
        self.assertIn("/api/blocking/disable?groups=ads&duration=30m", FakeBlocky.calls)

    def test_unreachable(self):
        old = adblock.BLOCKY_URL
        adblock.BLOCKY_URL = "http://127.0.0.1:9"
        try:
            with self.assertRaises(adblock.BlockyError):
                adblock.status()
        finally:
            adblock.BLOCKY_URL = old


if __name__ == "__main__":
    unittest.main()
