"""Custom DNS entries: hosts-file round trip, validation, Blocky query."""

import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from lanjaeger_webui import adblock, dnsentries


class Entries(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        (self.dir / "blocky").mkdir()
        self.file = self.dir / "blocky" / "hosts.txt"
        self.file.write_text("# header stays\n192.168.1.10 lanjaeger.lan cache.lan  # the cache\n")

    def test_read_splits_multiple_names(self):
        self.assertEqual(dnsentries.read(str(self.dir)), [
            {"name": "lanjaeger.lan", "ip": "192.168.1.10", "note": "the cache"},
            {"name": "cache.lan", "ip": "192.168.1.10", "note": "the cache"},
        ])

    def test_validate(self):
        clean, errors = dnsentries.validate([
            {"name": "NAS.lan.", "ip": "192.168.1.20", "note": "storage\n# x"},
            {"name": "cs2-server", "ip": "fd00::5"},
            {"name": "printer.local", "ip": "192.168.1.30"},
            {"name": "bad name", "ip": "192.168.1.31"},
            {"name": "ok.lan", "ip": "999.1.1.1"},
            {"name": "nas.lan", "ip": "192.168.1.20"},          # duplicate of row 0
        ])
        self.assertEqual(clean[0], {"name": "nas.lan", "ip": "192.168.1.20", "note": "storage   x"})
        self.assertEqual(clean[1]["ip"], "fd00::5")
        self.assertEqual(sorted(errors), [2, 3, 4, 5])
        self.assertIn("mDNS", errors[2])
        with self.assertRaises(ValueError):
            dnsentries.validate("nas.lan")

    def test_write_in_place_keeps_header_and_inode(self):
        inode = os.stat(self.file).st_ino
        dnsentries.write(str(self.dir), [{"name": "nas.lan", "ip": "192.168.1.20", "note": ""},
                                         {"name": "srv.lan", "ip": "192.168.1.21", "note": "game server"}])
        self.assertEqual(os.stat(self.file).st_ino, inode)   # still the file Blocky has mounted
        self.assertEqual(self.file.read_text(),
                         "# header stays\n192.168.1.20 nas.lan\n192.168.1.21 srv.lan  # game server\n")
        self.assertEqual(len(dnsentries.read(str(self.dir))), 2)


class FakeBlocky(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        out = json.dumps({"reason": "HOSTS FILE", "response": f"A ({body['query']}: 192.168.1.20)",
                          "responseType": "HOSTSFILE", "returnCode": "NOERROR"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


class Lookup(unittest.TestCase):
    def test_query_posts_json(self):
        srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeBlocky)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        old, adblock.BLOCKY_URL = adblock.BLOCKY_URL, f"http://127.0.0.1:{srv.server_address[1]}"
        try:
            r = adblock.query("nas.lan", "A")
        finally:
            adblock.BLOCKY_URL = old
            srv.shutdown()
        self.assertEqual((r["responseType"], r["returnCode"]), ("HOSTSFILE", "NOERROR"))
        self.assertIn("nas.lan", r["response"])


if __name__ == "__main__":
    unittest.main()
