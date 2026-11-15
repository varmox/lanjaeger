"""Prefill tests: settings, tool arguments, scheduler, login transcript scrubbing."""

import datetime as dt
import json
import os
import tempfile
import unittest
from pathlib import Path

from lanjaeger_webui import prefill


class PrefillTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        (self.dir / ".env").write_text("DNS_BIND_IP=192.168.1.10\n")
        self.runner = prefill.Runner(str(self.dir), "lj", str(self.dir / ".env"))

    def test_validate(self):
        ok = prefill.validate({"steam": {"enabled": True, "games": {"730": "Counter-Strike 2", "0570": "Dota 2"},
                                         "os": ["linux", "windows", "amiga"], "top": 20},
                               "battlenet": {"products": ["pro", "s2", "pro"]},
                               "schedule": {"enabled": True, "time": "02:30"}})
        self.assertEqual(ok["steam"]["games"], {"730": "Counter-Strike 2", "570": "Dota 2"})
        self.assertEqual(ok["steam"]["os"], ["linux", "windows"])
        self.assertEqual(ok["battlenet"]["products"], ["pro", "s2"])
        for bad in ({"steam": {"games": {"cs2": "x"}}}, {"steam": {"top": 500}},
                    {"battlenet": {"products": ["nope"]}}, {"schedule": {"time": "25:00"}}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                prefill.validate(bad)

    def test_args(self):
        st = prefill.validate({"steam": {"all": True, "top": 10, "os": ["windows", "linux"]}})["steam"]
        self.assertEqual(prefill.steam_args(st),
                         ["prefill", "--no-ansi", "--all", "--top", "10", "--os", "linux", "windows"])
        bn = prefill.validate({"battlenet": {"products": ["s2", "pro"]}})["battlenet"]
        self.assertEqual(prefill.battlenet_args(bn), ["prefill", "--no-ansi", "--products", "pro", "s2"])

    def test_save_writes_steam_selection(self):
        self.runner.store.save({"steam": {"enabled": True, "games": {"730": "CS2", "570": "Dota 2"}}})
        sel = json.loads((self.dir / "prefill" / "steam" / "config" / "selectedAppsToPrefill.json").read_text())
        self.assertEqual(sorted(sel), [570, 730])
        self.assertEqual(self.runner.store.load()["steam"]["games"]["730"], "CS2")

    def test_runnable_needs_login_for_steam(self):
        s = self.runner.store.save({"steam": {"enabled": True, "games": {"730": "CS2"}},
                                    "battlenet": {"enabled": True, "products": ["pro"]}})
        self.assertEqual(self.runner.runnable(s), ["battlenet"])
        (self.dir / "prefill" / "steam" / "config" / "account.config").write_bytes(b"token")
        self.assertEqual(self.runner.runnable(s), ["steam", "battlenet"])
        self.runner.store.logout("steam")
        self.assertFalse(self.runner.store.logged_in("steam"))

    def test_scheduler_fires_once_per_day_after_time(self):
        self.runner.store.save({"battlenet": {"enabled": True, "products": ["pro"]},
                                "schedule": {"enabled": True, "time": "03:00"}})
        started = []
        self.runner.start = lambda services: started.append(services) or services
        self.assertFalse(self.runner.tick(dt.datetime(2026, 11, 16, 2, 59)))
        self.assertTrue(self.runner.tick(dt.datetime(2026, 11, 16, 3, 0)))
        self.assertFalse(self.runner.tick(dt.datetime(2026, 11, 16, 4, 0)))   # already ran today
        self.assertTrue(self.runner.tick(dt.datetime(2026, 11, 17, 3, 1)))
        self.assertEqual(started, [["battlenet"], ["battlenet"]])

    def test_scheduler_disabled(self):
        self.runner.store.save({"battlenet": {"enabled": True, "products": ["pro"]}})
        self.runner.start = lambda s: self.fail("should not start")
        self.assertFalse(self.runner.tick(dt.datetime(2026, 11, 16, 3, 0)))

    def test_dns_from_env(self):
        self.assertEqual(self.runner.dns_ip(), "192.168.1.10")

    def test_strip_ansi_and_scrub(self):
        self.assertEqual(prefill.strip_ansi("\x1b[38;5;80mSteam\x1b[0m username: \r"), "Steam username: ")
        login = prefill.Login(self.runner, "steam")
        login.state = "running"
        login._secrets = ["hunter2"]
        login._append(b"echo hunter2\n")
        self.assertNotIn("hunter2", login.output)
        login.output = "Password: "
        self.assertTrue(login.status()["secret"])

    def test_send_validation(self):
        login = prefill.Login(self.runner, "steam")
        with self.assertRaises(RuntimeError):
            login.send("x", False)  # no session


if __name__ == "__main__":
    unittest.main()
