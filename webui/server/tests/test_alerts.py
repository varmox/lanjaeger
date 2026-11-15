"""Alerts: validation, secrets, state machine, channel payloads."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lanjaeger_webui import alerts


def settings(**over):
    s = json.loads(json.dumps(alerts.DEFAULTS))
    s.update(over)
    return s


class Validation(unittest.TestCase):
    def test_defaults_valid(self):
        alerts.validate(settings(), settings())

    def test_rejects_bad_values(self):
        cur = settings()
        bad = [
            {"rules": {"diskPercent": 120}},
            {"repeatHours": 0},
            {"channels": {"ntfy": {"enabled": True, "url": "ftp://x"}}},
            {"channels": {"discord": {"enabled": True, "webhook": "https://evil.example/hook"}}},
            {"channels": {"email": {"enabled": True, "host": "smtp.x", "from": "me", "to": "you@x.ch"}}},
            {"channels": {"ntfy": {"enabled": True, "url": ""}}},   # on but empty
        ]
        for b in bad:
            with self.subTest(b=b), self.assertRaises(ValueError):
                alerts.validate(b, cur)

    def test_secret_placeholder_keeps_stored_value(self):
        cur = settings()
        cur["channels"]["discord"].update(enabled=True, webhook="https://discord.com/api/webhooks/1/abc")
        new = alerts.validate({"channels": {"discord": {"enabled": True, "webhook": alerts.SECRET}}}, cur)
        self.assertEqual(new["channels"]["discord"]["webhook"], "https://discord.com/api/webhooks/1/abc")
        pub = alerts.public(new)
        self.assertEqual(pub["channels"]["discord"]["webhook"], alerts.SECRET)
        self.assertEqual(pub["channels"]["email"]["password"], "")


class StateMachine(unittest.TestCase):
    def setUp(self):
        self.a = alerts.Alerts(tempfile.mkdtemp(), "lj", "/")
        self.s = settings(repeatHours=2)

    def test_grace_fire_repeat_resolve(self):
        down = {"service:dns": {"severity": "critical", "title": "dns is exited", "body": "", "graceMinutes": 5}}
        self.assertEqual(self.a.evaluate(down, self.s, now=0), [])                 # pending
        self.assertEqual(self.a.evaluate(down, self.s, now=200), [])               # still in grace
        fired = self.a.evaluate(down, self.s, now=301)
        self.assertEqual([m["kind"] for m in fired], ["fired"])
        self.assertEqual(self.a.evaluate(down, self.s, now=3600), [])              # not yet repeat
        self.assertEqual([m["kind"] for m in self.a.evaluate(down, self.s, now=301 + 7200)], ["repeat"])
        resolved = self.a.evaluate({}, self.s, now=9000)
        self.assertEqual([(m["kind"], m["title"]) for m in resolved], [("resolved", "Resolved: dns is exited")])

    def test_flap_inside_grace_never_fires(self):
        down = {"service:dns": {"severity": "critical", "title": "x", "body": "", "graceMinutes": 5}}
        self.a.evaluate(down, self.s, now=0)
        self.a.evaluate({}, self.s, now=60)               # recovered within grace
        self.assertEqual(self.a.evaluate(down, self.s, now=120), [])

    def test_oneshot_sent_once_even_after_restart(self):
        f = {"prefill:steam:2026-11-16T03:10:00Z": {"severity": "warning", "title": "Prefill failed", "body": "",
                                                     "oneShot": True}}
        self.assertEqual(len(self.a.evaluate(f, self.s, now=0)), 1)
        self.assertEqual(self.a.evaluate(f, self.s, now=60), [])
        self.a.dispatch([{"key": "k", "kind": "fired", "severity": "warning", "title": "t"}], self.s)
        b = alerts.Alerts(os.path.dirname(self.a.dir), "lj", "/")  # restart: state reloaded
        self.assertEqual(b.evaluate(f, self.s, now=120), [])

    def test_dispatch_skips_sending_when_disabled(self):
        self.s["enabled"] = False
        with mock.patch.object(alerts, "notify") as n:
            self.a.dispatch([{"key": "disk", "kind": "fired", "severity": "warning", "title": "t", "body": "b"}], self.s)
        n.assert_not_called()
        self.assertEqual(self.a.history[-1]["title"], "t")


class Checks(unittest.TestCase):
    def test_disk(self):
        st = os.statvfs_result((4096, 4096, 1000, 50, 50, 0, 0, 0, 0, 255))   # 95% used
        with mock.patch.object(alerts.os, "statvfs", return_value=st):
            a = alerts.check_disk({"diskPercent": 90, "diskFreeGb": 0}, "/x")
        self.assertEqual(a["title"], "Cache disk almost full")
        st = os.statvfs_result((4096, 4096, 1000, 500, 500, 0, 0, 0, 0, 255))
        with mock.patch.object(alerts.os, "statvfs", return_value=st):
            self.assertIsNone(alerts.check_disk({"diskPercent": 90, "diskFreeGb": 0}, "/x"))

    def test_services(self):
        cs = [{"service": "dns", "state": "running", "health": "healthy", "image": "i", "status": "Up"},
              {"service": "monolithic", "state": "running", "health": "unhealthy", "image": "i", "status": "Up (unhealthy)"},
              {"service": "blocky", "state": "exited", "health": None, "image": "i", "status": "Exited (1)"},
              {"service": "webui", "state": "exited", "health": None, "image": "i", "status": "Exited"}]
        with mock.patch.object(alerts.docker_api, "containers", return_value=cs):
            out = alerts.check_services("lj")
        self.assertEqual(sorted(out), ["service:blocky", "service:logrotate", "service:monolithic"])
        self.assertIn("unhealthy", out["service:monolithic"]["title"])

    def test_prefill_ignores_stop_and_success(self):
        st = {"services": {
            "steam": {"lastRun": {"running": False, "exitCode": 1, "finishedAt": "T1"}, "log": "a\nerror: boom"},
            "epic": {"lastRun": {"running": False, "exitCode": 143, "finishedAt": "T2"}, "log": ""},
            "battlenet": {"lastRun": {"running": False, "exitCode": 0, "finishedAt": "T3"}, "log": ""}}}
        out = alerts.check_prefill(st)
        self.assertEqual(list(out), ["prefill:steam:T1"])
        self.assertIn("error: boom", out["prefill:steam:T1"]["body"])


class Channels(unittest.TestCase):
    def test_payloads(self):
        sent = []
        with mock.patch.object(alerts, "_post", lambda url, data, headers: sent.append((url, data, headers))):
            alerts.send_ntfy({"url": "https://ntfy.sh/lan", "token": "tk"}, "Disk full", "body", "critical")
            alerts.send_discord({"webhook": "https://discord.com/api/webhooks/1/x"}, "Disk full", "@everyone body", "warning")
        url, data, h = sent[0]
        self.assertEqual((url, data, h["Priority"], h["Authorization"]), ("https://ntfy.sh/lan", b"body", "5", "Bearer tk"))
        payload = json.loads(sent[1][1])
        self.assertIn("**Disk full**", payload["content"])
        self.assertEqual(payload["allowed_mentions"], {"parse": []})   # no @everyone pings

    def test_notify_reports_each_channel(self):
        s = settings()
        s["channels"]["ntfy"].update(enabled=True, url="https://ntfy.sh/x")
        s["channels"]["discord"].update(enabled=True, webhook="https://discord.com/api/webhooks/1/x")
        with mock.patch.object(alerts, "SENDERS", {"ntfy": lambda *a: None,
                                                   "discord": mock.Mock(side_effect=OSError("timeout")),
                                                   "email": lambda *a: None}):
            res = alerts.notify(s, "t", "b", "info")
        self.assertEqual(res["ntfy"], "ok")
        self.assertIn("timeout", res["discord"])
        self.assertNotIn("email", res)


if __name__ == "__main__":
    unittest.main()
