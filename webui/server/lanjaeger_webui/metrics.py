"""Prometheus metrics for Grafana, Telegraf and VCF Operations.

Served at /metrics (Prometheus text format), only with
`Authorization: Bearer <METRICS_TOKEN>`; without METRICS_TOKEN the endpoint
is off. Collected on each scrape from what the web UI already tracks: the
access-log statistics, cache disk, containers, prefill state and alerts.
Blocky's own metrics (DNS queries, blocked, cache) are passed through, as
Blocky's API port isn't reachable from outside the compose network.

Every important number also exists without labels (e.g.
lanjaeger_bytes_sent_to_clients_total), so tools that handle labels poorly
— VCF Operations — still get them. Per-client and per-game series are
prefixed lanjaeger_client_ / lanjaeger_prefill_game_ so they're easy to drop.
"""

from __future__ import annotations

import hmac
import os
import re
import urllib.request

from . import adblock, disk, docker_api, envfile, prefill

# Passed through from Blocky; per-client response counts are left out (the
# per-client query count is enough, and it keeps the series count down).
BLOCKY_KEEP = re.compile(r"^(# (HELP|TYPE) )?blocky_(?!client_response_total)")


def token_ok(header: str | None, token: str) -> bool:
    if not token or not header or not header.startswith("Bearer "):
        return False
    return hmac.compare_digest(header[7:].strip().encode(), token.encode())


def _esc(v) -> str:
    return str(v).replace("\\", "\\\\").replace("\"", "\\\"").replace("\n", "\\n")


class Writer:
    def __init__(self):
        self.lines: list[str] = []
        self._declared: set[str] = set()

    def add(self, name: str, kind: str, help_: str, value, labels: dict | None = None) -> None:
        if value is None:
            return
        if name not in self._declared:
            self._declared.add(name)
            self.lines.append(f"# HELP {name} {help_}")
            self.lines.append(f"# TYPE {name} {kind}")
        lbl = ""
        if labels:
            lbl = "{" + ",".join(f'{k}="{_esc(v)}"' for k, v in labels.items()) + "}"
        if isinstance(value, bool):
            value = int(value)
        self.lines.append(f"{name}{lbl} {value}")

    def text(self) -> str:
        return "\n".join(self.lines) + "\n"


def cache_metrics(w: Writer, snap: dict, live: dict | None) -> None:
    # Counters: traffic since the web UI started (see Stats.mark_live).
    if live is not None:
        hit, miss = live["total"]["hitBytes"], live["total"]["missBytes"]
        w.add("lanjaeger_bytes_sent_to_clients_total", "counter",
              "Bytes the cache sent to clients (outbound to the LAN)", hit + miss)
        w.add("lanjaeger_bytes_from_internet_total", "counter",
              "Bytes fetched from the internet for cache misses (inbound)", miss)
        w.add("lanjaeger_bytes_from_cache_total", "counter", "Bytes served from the cache disk (hits)", hit)
        w.add("lanjaeger_requests_total", "counter", "Download requests served", live["total"]["requests"])
        for name, c in sorted(live["services"].items()):
            for status, key in (("hit", "hitBytes"), ("miss", "missBytes")):
                w.add("lanjaeger_service_bytes_total", "counter", "Bytes sent to clients per game service",
                      c[key], {"service": name, "status": status})
            w.add("lanjaeger_service_requests_total", "counter", "Requests per game service",
                  c["requests"], {"service": name})
        for ip, c in sorted(live["clients"].items()):
            for status, key in (("hit", "hitBytes"), ("miss", "missBytes")):
                w.add("lanjaeger_client_bytes_total", "counter", "Bytes sent per client",
                      c[key], {"client": ip, "status": status})

    # Gauges: the whole statistics window (log tail + everything since).
    total = snap["total"]
    hit, miss = total["hitBytes"], total["missBytes"]
    w.add("lanjaeger_cache_hit_ratio", "gauge", "Share of bytes served from cache, whole statistics window (0-1)",
          round(hit / (hit + miss), 4) if hit + miss else 0)
    w.add("lanjaeger_window_bytes_sent_to_clients", "gauge", "Bytes sent to clients, whole statistics window",
          hit + miss)
    w.add("lanjaeger_window_bytes_from_internet", "gauge", "Bytes fetched from the internet, whole statistics window",
          miss)
    active = snap["active"]
    w.add("lanjaeger_clients_seen", "gauge", "Distinct clients that downloaded through the cache", len(snap["clients"]))
    w.add("lanjaeger_clients_active", "gauge", "Clients downloading right now (last minute)",
          len({a["client"] for a in active}))
    w.add("lanjaeger_download_rate_bytes", "gauge", "Current download rate from the cache to clients (bytes/s)",
          round(sum(a["bytesPerSecond"] for a in active)))
    if snap["since"]:
        w.add("lanjaeger_stats_since_timestamp_seconds", "gauge", "Oldest log entry in the statistics",
              int(snap["since"]))


def disk_metrics(w: Writer, d: dict) -> None:
    w.add("lanjaeger_cache_size_bytes", "gauge", "Size of the cache on disk", d["cacheBytes"])
    w.add("lanjaeger_cache_limit_bytes", "gauge", "CACHE_DISK_SIZE", d["cacheLimit"])
    w.add("lanjaeger_cache_filesystem_size_bytes", "gauge", "Size of the cache filesystem", d["filesystemTotal"])
    w.add("lanjaeger_cache_filesystem_free_bytes", "gauge", "Free space on the cache filesystem", d["filesystemFree"])


def container_metrics(w: Writer, containers: list[dict]) -> None:
    unhealthy = 0
    for c in containers:
        up = c["state"] == "running"
        healthy = up and c["health"] in (None, "healthy")
        unhealthy += not healthy
        lbl = {"service": c["service"]}
        w.add("lanjaeger_container_up", "gauge", "Container is running (1) or not (0)", up, lbl)
        w.add("lanjaeger_container_healthy", "gauge",
              "Running and healthy, or running without a health check (1)", healthy, lbl)
    w.add("lanjaeger_containers_unhealthy", "gauge", "Containers not running or not healthy", unhealthy)


def prefill_metrics(w: Writer, runner: prefill.Runner) -> None:
    games = prefill.prefilled(runner.store)
    runs = runner.last_runs()
    for service in prefill.SERVICE_ORDER:
        lbl = {"service": service}
        w.add("lanjaeger_prefill_logged_in", "gauge", "Prefill tool is logged in (Battle.net needs no login)",
              service == "battlenet" or runner.store.logged_in(service), lbl)
        w.add("lanjaeger_prefill_games", "gauge", "Games prefilled at least once",
              sum(1 for g in games if g["service"] == service), lbl)
        run = runs.get(service)
        w.add("lanjaeger_prefill_running", "gauge", "Prefill running now", bool(run and run["running"]), lbl)
        if run and not run["running"]:
            w.add("lanjaeger_prefill_last_run_success", "gauge", "Last prefill run exited with 0",
                  run["exitCode"] == 0, lbl)
            if run["finished"]:
                w.add("lanjaeger_prefill_last_run_timestamp_seconds", "gauge", "When the last prefill run finished",
                      run["finished"], lbl)
    for g in games:
        w.add("lanjaeger_prefill_game_timestamp_seconds", "gauge",
              "When a game was last prefilled (per game)", g["time"],
              {"service": g["service"], "id": g["id"], "name": g["name"]})
    depots = prefill.steam_depot_count(runner.store)
    w.add("lanjaeger_prefill_steam_depots", "gauge", "Steam depots the Steam prefill has downloaded", depots)


def blocky_metrics(w: Writer) -> None:
    try:
        req = urllib.request.Request(adblock.BLOCKY_URL + "/metrics")
        with urllib.request.urlopen(req, timeout=5) as r:
            text = r.read().decode("utf-8", "replace")
        w.add("lanjaeger_dns_up", "gauge", "Blocky (the DNS server) answers", 1)
    except OSError:
        w.add("lanjaeger_dns_up", "gauge", "Blocky (the DNS server) answers", 0)
        return
    w.lines.extend(line for line in text.splitlines() if BLOCKY_KEEP.match(line))


def render(ctx) -> str:
    w = Writer()
    w.add("lanjaeger_up", "gauge", "The lanjaeger web UI answers", 1)
    cache_metrics(w, ctx.stats.snapshot(), ctx.stats.live_counters())
    limit = envfile.read(ctx.env_path).get("CACHE_DISK_SIZE") if ctx.env_path else None
    disk_metrics(w, disk.usage(ctx.cache_dir, ctx.cache_size, limit or os.environ.get("CACHE_DISK_SIZE")))
    if ctx.docker and ctx.project:
        try:
            container_metrics(w, [c for c in docker_api.containers(ctx.project)
                                  if not c["service"].startswith(f"{ctx.project}-prefill-")])
        except docker_api.DockerError:
            pass
    if ctx.prefill:
        try:
            prefill_metrics(w, ctx.prefill)
        except (OSError, docker_api.DockerError):
            pass
    if ctx.alerts:
        w.add("lanjaeger_alerts_active", "gauge", "Alerts currently firing", len(ctx.alerts.snapshot()["active"]))
    blocky_metrics(w)
    return w.text()
