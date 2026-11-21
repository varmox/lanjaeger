"""Metrics: Prometheus text format, cache/container numbers, prefilled games."""

import json
import os
import tempfile
import time
import unittest

from lanjaeger_webui import metrics, prefill
from lanjaeger_webui.stats import Stats


def parse(text: str) -> dict:
    """{'name{labels}': value} for every sample line."""
    out = {}
    for line in text.splitlines():
        if line and not line.startswith("#"):
            key, value = line.rsplit(" ", 1)
            out[key] = float(value)
    return out


class Format(unittest.TestCase):
    def test_help_type_once_and_label_escaping(self):
        w = metrics.Writer()
        w.add("x_total", "counter", "help", 1, {"name": 'Say "hi"\\now'})
        w.add("x_total", "counter", "help", 2, {"name": "b"})
        w.add("skipped", "gauge", "none", None)
        lines = w.text().splitlines()
        self.assertEqual(sum(1 for line in lines if line.startswith("# TYPE x_total")), 1)
        self.assertIn('x_total{name="Say \\"hi\\"\\\\now"} 1', lines)
        self.assertNotIn("skipped", w.text())

    def test_token(self):
        self.assertTrue(metrics.token_ok("Bearer s3cret", "s3cret"))
        self.assertFalse(metrics.token_ok("Bearer wrong", "s3cret"))
        self.assertFalse(metrics.token_ok(None, "s3cret"))
        self.assertFalse(metrics.token_ok("Bearer ", ""))

    def test_blocky_passthrough_filter(self):
        keep = [line for line in [
            "# HELP blocky_query_total x", "blocky_query_total{client=\"a\",type=\"A\"} 3",
            "blocky_client_response_total{client=\"a\"} 3", "go_goroutines 12", "# TYPE process_cpu x",
        ] if metrics.BLOCKY_KEEP.match(line)]
        self.assertEqual(keep, ["# HELP blocky_query_total x", "blocky_query_total{client=\"a\",type=\"A\"} 3"])


class Numbers(unittest.TestCase):
    def test_cache_metrics(self):
        s, now = Stats(), time.time()
        s.ingest({"service": "steam", "client": "10.0.0.9", "ts": now, "bytes": 5000, "hit": True, "status": 200})
        s.mark_live()   # the 5000 bytes above were the startup replay
        for client, hit, b in (("10.0.0.5", True, 300), ("10.0.0.5", False, 100), ("10.0.0.6", True, 600)):
            s.ingest({"service": "steam", "client": client, "ts": now, "bytes": b, "hit": hit, "status": 200})
        w = metrics.Writer()
        metrics.cache_metrics(w, s.snapshot(now), s.live_counters())
        m = parse(w.text())
        self.assertEqual(m["lanjaeger_bytes_sent_to_clients_total"], 1000)      # replay not counted
        self.assertEqual(m["lanjaeger_bytes_from_internet_total"], 100)
        self.assertEqual(m["lanjaeger_window_bytes_sent_to_clients"], 6000)     # whole window
        self.assertEqual(m["lanjaeger_cache_hit_ratio"], 0.9833)
        self.assertEqual(m["lanjaeger_clients_seen"], 3)
        self.assertEqual(m["lanjaeger_clients_active"], 3)
        self.assertEqual(m['lanjaeger_service_bytes_total{service="steam",status="miss"}'], 100)
        self.assertEqual(m['lanjaeger_client_bytes_total{client="10.0.0.6",status="hit"}'], 600)
        self.assertEqual(m['lanjaeger_client_bytes_total{client="10.0.0.9",status="hit"}'], 0)

    def test_no_counters_during_replay(self):
        s = Stats()
        w = metrics.Writer()
        metrics.cache_metrics(w, s.snapshot(), s.live_counters())
        self.assertNotIn("lanjaeger_bytes_sent_to_clients_total", w.text())
        self.assertIn("lanjaeger_clients_seen 0", w.text())

    def test_containers(self):
        w = metrics.Writer()
        metrics.container_metrics(w, [
            {"service": "dns", "state": "running", "health": "healthy"},
            {"service": "logrotate", "state": "running", "health": None},
            {"service": "monolithic", "state": "running", "health": "unhealthy"},
            {"service": "blocky", "state": "exited", "health": None},
        ])
        m = parse(w.text())
        self.assertEqual(m['lanjaeger_container_healthy{service="logrotate"}'], 1)
        self.assertEqual(m['lanjaeger_container_healthy{service="monolithic"}'], 0)
        self.assertEqual(m['lanjaeger_container_up{service="blocky"}'], 0)
        self.assertEqual(m["lanjaeger_containers_unhealthy"], 2)


class Prefilled(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = prefill.Store(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, rel: str, data) -> None:
        path = os.path.join(self.store.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(data if isinstance(data, str) else json.dumps(data))

    def test_all_three_services(self):
        prefill.record_steam_success(self.store, {"730": "Counter-Strike 2"}, now=1000)
        prefill.record_steam_success(self.store, {"570": "Dota 2"}, now=2000)
        self.write("steam/config/successfullyDownloadedDepots.json", {"731": [1], "732": [2]})
        self.write("epic/config/successfullyDownloadedApps.json", {"fn": ["1.0"], "unknown": ["2"]})
        self.write("epic/library.json", {"games": [{"id": "fn", "title": "Fortnite"}]})
        self.write("battlenet/cache/BattlenetPrefill/v1/prefilledVersion-pro.txt", "2.15")
        self.write("battlenet/cache/BattlenetPrefill/v1/other.idx", "x")

        games = {(g["service"], g["id"]): g for g in prefill.prefilled(self.store)}
        self.assertEqual(set(games), {("steam", "730"), ("steam", "570"), ("epic", "fn"), ("epic", "unknown"),
                                      ("battlenet", "pro")})
        self.assertEqual(games[("steam", "730")]["time"], 1000)
        self.assertEqual(games[("epic", "fn")]["name"], "Fortnite")
        self.assertEqual(games[("epic", "unknown")]["name"], "unknown")
        self.assertEqual(games[("battlenet", "pro")]["name"], "Overwatch 2")
        self.assertEqual(prefill.steam_depot_count(self.store), 2)

    def test_nothing_prefilled_yet(self):
        self.assertEqual(prefill.prefilled(self.store), [])
        self.assertIsNone(prefill.steam_depot_count(self.store))

    def test_docker_time(self):
        self.assertEqual(prefill._docker_time("1970-01-02T00:00:00.123456789Z"), 86400)
        self.assertIsNone(prefill._docker_time("0001-01-01T00:00:00Z"))


if __name__ == "__main__":
    unittest.main()
