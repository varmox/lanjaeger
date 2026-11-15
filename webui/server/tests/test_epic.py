"""Epic prefill: token handling, library listing, selection safety."""

import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lanjaeger_webui import prefill


def token(minutes_left: int) -> dict:
    exp = dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=minutes_left)
    # .NET style: 7 fractional digits and a Z
    return {"access_token": "AT", "refresh_token": "RT",
            "expires_at": exp.strftime("%Y-%m-%dT%H:%M:%S.1234567Z")}


class EpicTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        (self.dir / ".env").write_text("DNS_BIND_IP=192.168.1.10\n")
        self.runner = prefill.Runner(str(self.dir), "lj", str(self.dir / ".env"))
        self.cfg = Path(self.runner.store.dir("epic"))

    def write_token(self, minutes_left):
        (self.cfg / "userAccount.json").write_text(json.dumps(token(minutes_left)))

    def test_token_valid_and_expired(self):
        with self.assertRaises(prefill.TokenExpired):
            prefill.epic_access_token(self.runner.store)   # not logged in
        self.write_token(60)
        self.assertEqual(prefill.epic_access_token(self.runner.store), "AT")
        self.write_token(1)                                 # about to expire: never use
        with self.assertRaises(prefill.TokenExpired):
            prefill.epic_access_token(self.runner.store)

    def test_fetch_library_filters_and_caches_titles(self):
        self.write_token(60)
        assets = [
            {"appName": "Fortnite", "namespace": "fn", "catalogItemId": "c1"},
            {"appName": "UE_Asset", "namespace": "ue", "catalogItemId": "c2"},
            {"appName": "Satisfactory", "namespace": "crab", "catalogItemId": "c3"},
        ]
        calls = []

        def fake_get(url, tok):
            calls.append(url)
            self.assertEqual(tok, "AT")
            if "/assets/" in url:
                return assets
            cid = url.split("id=")[1].split("&")[0]
            return {cid: {"title": {"c1": "Fortnite", "c3": "Satisfactory"}[cid]}}

        with mock.patch.object(prefill, "_epic_get", fake_get):
            lib = prefill.fetch_epic_library(self.runner.store)
            self.assertEqual([g["id"] for g in lib["games"]], ["Fortnite", "Satisfactory"])
            calls.clear()
            prefill.fetch_epic_library(self.runner.store)       # titles now cached
            self.assertEqual(len(calls), 1)
        self.assertEqual(prefill.read_epic_library(self.runner.store)["games"][1]["title"], "Satisfactory")

    def test_settings_selection_and_args(self):
        s = self.runner.store.save({"epic": {"enabled": True, "games": {"Fortnite": "Fortnite", "Gone": "Old game"}}})
        self.assertEqual(json.loads((self.cfg / "selectedAppsToPrefill.json").read_text()), ["Fortnite", "Gone"])
        self.assertEqual(prefill.epic_args(s["epic"]), ["prefill", "--no-ansi"])
        self.assertEqual(prefill.epic_args({**s["epic"], "all": True}), ["prefill", "--no-ansi", "--all"])
        with self.assertRaises(ValueError):
            prefill.validate({"epic": {"games": {"bad id; rm": "x"}}})

    def test_runnable_needs_login(self):
        s = self.runner.store.save({"epic": {"enabled": True, "all": True}})
        self.assertEqual(self.runner.runnable(s), [])
        self.write_token(60)
        self.assertEqual(self.runner.runnable(s), ["epic"])

    def test_prune_drops_games_no_longer_owned(self):
        s = self.runner.store.save({"epic": {"enabled": True, "games": {"Fortnite": "Fortnite", "Gone": "Old"}}})
        lib = self.dir / "prefill" / "epic" / "library.json"
        lib.write_text(json.dumps({"updated": "x", "games": [{"id": "Fortnite", "title": "Fortnite"}]}))
        self.runner._prune_epic_selection(s)
        self.assertEqual(json.loads((self.cfg / "selectedAppsToPrefill.json").read_text()), ["Fortnite"])

    def test_login_swaps_selection_out_and_back(self):
        self.runner.store.save({"epic": {"enabled": True, "games": {"Fortnite": "Fortnite"}}})
        login = prefill.Login(self.runner, "epic")
        login._swap_out_selection()
        self.assertEqual(json.loads((self.cfg / "selectedAppsToPrefill.json").read_text()), [])
        login._restore_selection()
        self.assertEqual(json.loads((self.cfg / "selectedAppsToPrefill.json").read_text()), ["Fortnite"])

    def test_auth_code_prompt_is_secret(self):
        login = prefill.Login(self.runner, "epic")
        login.state = "running"
        login.output = "Please enter the authorizationCode from the JSON response:"
        self.assertTrue(login.status()["secret"])

    def test_refresh_refuses_while_busy(self):
        self.runner.current = "steam"
        with self.assertRaises(RuntimeError):
            self.runner.refresh_epic_library()


if __name__ == "__main__":
    unittest.main()
