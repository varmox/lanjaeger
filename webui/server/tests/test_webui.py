"""Backend tests: log parsing/following, .env editing, auth, docker log demux, HTTP API.

    cd webui/server && python3 -m unittest -v
"""

import json
import os
import struct
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from lanjaeger_webui import app, auth, disk, docker_api, envfile, stats

TEXT = ('[steam] 10.0.0.5 / - - - [15/Nov/2026:12:00:00 +0000] "GET /depot/1/chunk/a HTTP/1.1" 200 {b} '
        '"-" "Valve/Steam HTTP Client 1.0" "{c}" "lancache.steamcontent.com" "bytes=0-1048575"')


class Parsing(unittest.TestCase):
    def test_text_format(self):
        r = stats.parse_line(TEXT.format(b=1048576, c="HIT"))
        self.assertEqual((r["service"], r["client"], r["bytes"], r["hit"]), ("steam", "10.0.0.5", 1048576, True))
        self.assertEqual(r["ts"], 1794744000.0)
        self.assertFalse(stats.parse_line(TEXT.format(b=5, c="MISS"))["hit"])

    def test_json_format(self):
        line = json.dumps({"timestamp": "1794744000.123", "cache_identifier": "epicgames",
                           "remote_addr": "10.0.0.9", "bytes_sent": 42, "upstream_cache_status": "HIT",
                           "status": "206"})
        r = stats.parse_line(line)
        self.assertEqual((r["service"], r["bytes"], r["hit"], r["status"]), ("epicgames", 42, True, 206))

    def test_garbage(self):
        self.assertIsNone(stats.parse_line("not a log line"))
        self.assertIsNone(stats.parse_line("{broken json"))


class Following(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.log = self.dir / "access.log"
        self.s = stats.Stats()
        self.f = stats.LogFollower(str(self.log), self.s)

    def write(self, *lines, mode="a"):
        with open(self.log, mode) as fh:
            fh.write("".join(l + "\n" for l in lines))

    def test_follow_truncate_and_partial_lines(self):
        self.write(TEXT.format(b=100, c="HIT"), TEXT.format(b=50, c="MISS"))
        self.assertEqual(self.f.poll(), 2)
        with open(self.log, "a") as fh:      # half a line
            fh.write(TEXT.format(b=7, c="HIT")[:40])
        self.assertEqual(self.f.poll(), 0)
        with open(self.log, "a") as fh:
            fh.write(TEXT.format(b=7, c="HIT")[40:] + "\n")
        self.assertEqual(self.f.poll(), 1)
        self.write(TEXT.format(b=1000, c="HIT"), mode="w")  # logrotate truncation (+ new line)
        self.assertEqual(self.f.poll(), 1)
        t = self.s.snapshot(now=1794744100)["total"]
        self.assertEqual((t["hitBytes"], t["missBytes"], t["requests"]), (1107, 50, 4))

    def test_snapshot_shape(self):
        self.write(TEXT.format(b=100, c="HIT"), TEXT.format(b=300, c="MISS"))
        self.f.poll()
        snap = self.s.snapshot(now=1794744030)
        self.assertEqual(snap["services"][0]["name"], "steam")
        self.assertEqual(snap["clients"][0]["ip"], "10.0.0.5")
        self.assertEqual(len(snap["series"]), 24 * 12)
        self.assertEqual(sum(p["hit"] for p in snap["series"]), 100)
        self.assertEqual(snap["active"][0]["service"], "steam")


class EnvFile(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / ".env"
        self.path.write_text("# comment stays\nLANCACHE_IP=10.0.0.1\nCACHE_DISK_SIZE=2000g\nCACHE_ROOT=/srv/cache\n")
        os.chmod(self.path, 0o600)

    def test_validate(self):
        clean, errors = envfile.validate({
            "LANCACHE_IP": "10.0.0.2  10.0.0.3", "UPSTREAM_DNS": "1.1.1.1;8.8.8.8",
            "CACHE_DISK_SIZE": "4t", "CACHE_ROOT": "/", "TZ": "Europe/Zurich", "DNS_BIND_IP": "10.0.0.1\nX=1",
        })
        self.assertEqual(clean["LANCACHE_IP"], "10.0.0.2 10.0.0.3")
        self.assertEqual(clean["UPSTREAM_DNS"], "1.1.1.1; 8.8.8.8")
        self.assertEqual(clean["TZ"], "Europe/Zurich")
        self.assertIn("never t", errors["CACHE_DISK_SIZE"])
        self.assertIn("not editable", errors["CACHE_ROOT"])
        self.assertIn("DNS_BIND_IP", errors)

    def test_write_preserves_file(self):
        changed = envfile.write(str(self.path), {"CACHE_DISK_SIZE": "4000g", "LOG_KEEP": "3", "LANCACHE_IP": "10.0.0.1"})
        self.assertEqual(sorted(changed), ["CACHE_DISK_SIZE", "LOG_KEEP"])
        text = self.path.read_text()
        self.assertTrue(text.startswith("# comment stays\n"))
        self.assertIn("CACHE_DISK_SIZE=4000g\n", text)
        self.assertIn("CACHE_ROOT=/srv/cache\n", text)
        self.assertTrue(text.endswith("LOG_KEEP=3\n"))
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)


class Auth(unittest.TestCase):
    def test_login_and_lockout(self):
        a = auth.Auth("s3cret")
        self.assertIsNone(a.login("1.1.1.1", "nope"))
        tok = a.login("1.1.1.1", "s3cret")
        self.assertTrue(a.valid(tok))
        a.logout(tok)
        self.assertFalse(a.valid(tok))
        for _ in range(auth.MAX_ATTEMPTS):
            a.login("2.2.2.2", "bad")
        self.assertGreater(a.locked_out("2.2.2.2"), 0)
        self.assertIsNone(a.login("2.2.2.2", "s3cret"))  # correct but locked

    def test_disabled_without_password(self):
        self.assertIsNone(auth.Auth("").login("1.1.1.1", ""))

    def test_csrf(self):
        self.assertTrue(auth.csrf_ok("GET", {}))
        self.assertFalse(auth.csrf_ok("POST", {}))
        self.assertTrue(auth.csrf_ok("POST", {"X-Lanjaeger": "1", "Origin": "http://h:8080", "Host": "h:8080"}))
        self.assertFalse(auth.csrf_ok("POST", {"X-Lanjaeger": "1", "Origin": "http://evil", "Host": "h:8080"}))


class Misc(unittest.TestCase):
    def test_demux(self):
        raw = b"\x01\0\0\0" + struct.pack(">I", 3) + b"out" + b"\x02\0\0\0" + struct.pack(">I", 4) + b"err\n"
        self.assertEqual(docker_api.demux(raw), "outerr\n")
        self.assertEqual(docker_api.demux(b"plain tty output"), "plain tty output")

    def test_sizes(self):
        self.assertEqual(disk.parse_size("2000g"), 2000 * 1024 ** 3)
        self.assertEqual(disk.parse_size("500m"), 500 * 1024 ** 2)
        self.assertIsNone(disk.parse_size("lots"))


class Api(unittest.TestCase):
    """The real handler over HTTP, with Docker absent."""

    @classmethod
    def setUpClass(cls):
        root = Path(tempfile.mkdtemp())
        (root / "logs").mkdir(); (root / "cache").mkdir(); (root / "static").mkdir()
        (root / "static" / "index.html").write_text("<!doctype html><title>lanjaeger</title>")
        (root / ".env").write_text("CACHE_DISK_SIZE=100g\nRESTART_POLICY=no\n")
        (root / "logs" / "access.log").write_text(TEXT.format(b=10, c="HIT") + "\n")
        env = {"WEBUI_PASSWORD": "pw", "PROJECT_DIR": str(root), "LOG_DIR": str(root / "logs"),
               "CACHE_DIR": str(root / "cache"), "STATIC_DIR": str(root / "static"),
               "DOCKER_SOCK": str(root / "no.sock")}
        cls._env = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        docker_api.SOCKET = env["DOCKER_SOCK"]
        ctx = app.Context()
        ctx.start_background()
        app.Handler.ctx = ctx
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.root = root
        time.sleep(0.3)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        for k, v in cls._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def call(self, method, path, body=None, cookie=None, csrf=True):
        req = urllib.request.Request(self.base + path, method=method,
                                     data=json.dumps(body).encode() if body is not None else None)
        req.add_header("Content-Type", "application/json")
        if csrf:
            req.add_header("X-Lanjaeger", "1")
        if cookie:
            req.add_header("Cookie", cookie)
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read() or b"null"), r.headers
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"null"), e.headers

    def test_flow(self):
        s, data, _ = self.call("GET", "/api/session")
        self.assertEqual((s, data["signedIn"], data["controls"]), (200, False, False))
        self.assertEqual(self.call("GET", "/api/stats")[0], 401)
        self.assertEqual(self.call("POST", "/api/login", {"password": "pw"}, csrf=False)[0], 403)
        self.assertEqual(self.call("POST", "/api/login", {"password": "x"})[0], 401)
        s, _, h = self.call("POST", "/api/login", {"password": "pw"})
        self.assertEqual(s, 200)
        cookie = h["Set-Cookie"].split(";")[0]
        self.assertIn("HttpOnly", h["Set-Cookie"])
        self.assertIn("SameSite=Strict", h["Set-Cookie"])
        time.sleep(2.5)  # let the follower read the log
        s, data, _ = self.call("GET", "/api/stats", cookie=cookie)
        self.assertEqual(data["total"]["hitBytes"], 10)
        s, data, _ = self.call("GET", "/api/disk", cookie=cookie)
        self.assertEqual(data["cacheLimit"], 100 * 1024 ** 3)
        s, data, _ = self.call("PUT", "/api/config", {"values": {"CACHE_DISK_SIZE": "4t"}}, cookie=cookie)
        self.assertEqual((s, list(data["fields"])), (422, ["CACHE_DISK_SIZE"]))
        s, data, _ = self.call("PUT", "/api/config", {"values": {"RESTART_POLICY": "unless-stopped"}}, cookie=cookie)
        self.assertEqual((s, data["changed"]), (200, ["RESTART_POLICY"]))
        self.assertIn("RESTART_POLICY=unless-stopped", (self.root / ".env").read_text())
        s, data, _ = self.call("GET", "/api/services", cookie=cookie)
        self.assertFalse(data["available"])

    def test_static_and_headers(self):
        with urllib.request.urlopen(self.base + "/some/client/route") as r:
            self.assertIn(b"lanjaeger", r.read())
            self.assertIn("frame-ancestors 'none'", r.headers["Content-Security-Policy"])
        with urllib.request.urlopen(self.base + "/../../etc/passwd") as r:
            self.assertIn(b"lanjaeger", r.read())  # traversal falls back to index.html


if __name__ == "__main__":
    unittest.main()
