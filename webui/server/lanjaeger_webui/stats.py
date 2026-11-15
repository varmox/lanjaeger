"""Cache statistics from the monolithic access log.

Follows /data/logs/access.log like `tail -F`: survives truncation by the
logrotate service and file replacement, and on startup replays the tail of
the existing log so the dashboard isn't empty after a restart.

Understands both monolithic log formats: the default `cachelog` text format
and `cachelog-json` (NGINX_LOG_FORMAT=cachelog-json).
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import threading
import time
from collections import defaultdict

# [steam] 10.0.0.5 / - - - [15/Nov/2026:12:00:00 +0000] "GET /depot/1 HTTP/1.1" 200 1048576 "-" "Valve/Steam HTTP Client 1.0" "HIT" "lancache.steamcontent.com" "bytes=0-1048575"
TEXT_LINE = re.compile(
    r'^\[(?P<service>[^\]]*)\] (?P<client>\S+) / \S+ - \S+ \[(?P<time>[^\]]+)\] '
    r'"[^"]*" (?P<status>\d{3}) (?P<bytes>\d+) "[^"]*" "[^"]*" "(?P<cache>[^"]*)" "(?P<host>[^"]*)"'
)

# Statuses that mean the bytes came from local disk.
HIT_STATUSES = {"HIT", "STALE", "UPDATING", "REVALIDATED"}

BUCKET_S = 300            # 5-minute chart buckets
HISTORY_S = 24 * 3600     # keep 24 h of buckets
ACTIVE_S = 60             # "downloading now" window
BOOTSTRAP_BYTES = 64 * 1024 * 1024


def parse_line(line: str) -> dict | None:
    line = line.strip()
    if not line:
        return None
    if line.startswith("{"):
        try:
            j = json.loads(line)
            return {
                "service": j.get("cache_identifier") or "-",
                "client": j.get("remote_addr") or "-",
                "ts": float(j["timestamp"]),
                "bytes": int(j.get("bytes_sent") or 0),
                "hit": (j.get("upstream_cache_status") or "") in HIT_STATUSES,
                "status": int(j.get("status") or 0),
            }
        except (ValueError, KeyError, TypeError):
            return None
    m = TEXT_LINE.match(line)
    if not m:
        return None
    try:
        ts = dt.datetime.strptime(m["time"], "%d/%b/%Y:%H:%M:%S %z").timestamp()
    except ValueError:
        return None
    return {
        "service": m["service"] or "-",
        "client": m["client"],
        "ts": ts,
        "bytes": int(m["bytes"]),
        "hit": m["cache"] in HIT_STATUSES,
        "status": int(m["status"]),
    }


def _counter():
    return {"hitBytes": 0, "missBytes": 0, "requests": 0, "lastSeen": 0.0}


class Stats:
    """Thread-safe aggregates. `ingest` from the reader thread, `snapshot` from requests."""

    def __init__(self):
        self._lock = threading.Lock()
        self.since = None
        self.total = _counter()
        self.services: dict[str, dict] = defaultdict(_counter)
        self.clients: dict[str, dict] = defaultdict(_counter)
        self.buckets: dict[int, list[int]] = {}           # start → [hit, miss]
        self.recent: list[tuple[float, str, str, int]] = []  # (ts, client, service, bytes)

    def ingest(self, rec: dict) -> None:
        if rec["status"] >= 400:
            return
        key = "hitBytes" if rec["hit"] else "missBytes"
        b, ts = rec["bytes"], rec["ts"]
        with self._lock:
            if self.since is None or ts < self.since:
                self.since = ts
            for c in (self.total, self.services[rec["service"]], self.clients[rec["client"]]):
                c[key] += b
                c["requests"] += 1
                c["lastSeen"] = max(c["lastSeen"], ts)
            bucket = int(ts // BUCKET_S * BUCKET_S)
            slot = self.buckets.setdefault(bucket, [0, 0])
            slot[0 if rec["hit"] else 1] += b
            self.recent.append((ts, rec["client"], rec["service"], b))

    def _prune(self, now: float) -> None:
        cutoff = now - HISTORY_S
        for k in [k for k in self.buckets if k < cutoff]:
            del self.buckets[k]
        self.recent = [r for r in self.recent if r[0] >= now - ACTIVE_S]

    def snapshot(self, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        with self._lock:
            self._prune(now)
            active: dict[tuple[str, str], int] = defaultdict(int)
            for _, client, service, b in self.recent:
                active[(client, service)] += b
            start = int((now - HISTORY_S) // BUCKET_S * BUCKET_S) + BUCKET_S
            series = [
                {"t": t, "hit": self.buckets.get(t, [0, 0])[0], "miss": self.buckets.get(t, [0, 0])[1]}
                for t in range(start, int(now) + 1, BUCKET_S)
            ]

            def rows(d, label):
                return sorted(
                    ({label: k, **v} for k, v in d.items()),
                    key=lambda r: r["hitBytes"] + r["missBytes"], reverse=True,
                )

            return {
                "since": self.since,
                "total": dict(self.total),
                "services": rows(self.services, "name"),
                "clients": rows(self.clients, "ip"),
                "series": series,
                "bucketSeconds": BUCKET_S,
                "active": sorted(
                    ({"client": c, "service": s, "bytesPerSecond": b / ACTIVE_S}
                     for (c, s), b in active.items()),
                    key=lambda r: r["bytesPerSecond"], reverse=True,
                ),
            }


class LogFollower(threading.Thread):
    """Feeds new access-log lines into Stats, tolerating rotation."""

    def __init__(self, path: str, stats: Stats, interval: float = 2.0):
        super().__init__(daemon=True, name="log-follower")
        self.path, self.stats, self.interval = path, stats, interval
        self._pos = 0
        self._inode = None
        self._partial = ""

    def _open_position(self, size: int, first: bool) -> int:
        # First open: replay only the tail so startup stays fast on big logs.
        return max(0, size - BOOTSTRAP_BYTES) if first else 0

    def poll(self) -> int:
        """Read whatever is new. Returns the number of lines ingested."""
        try:
            st = os.stat(self.path)
        except FileNotFoundError:
            return 0
        first = self._inode is None
        if first or st.st_ino != self._inode or st.st_size < self._pos:
            # New file, replaced file, or truncated by logrotate.
            self._pos = self._open_position(st.st_size, first)
            self._inode = st.st_ino
            self._partial = ""
            skip_partial = first and self._pos > 0
        else:
            skip_partial = False
        if st.st_size == self._pos:
            return 0
        n = 0
        with open(self.path, "rb") as f:  # binary: positions must be byte offsets
            f.seek(self._pos)
            chunk = f.read(16 * 1024 * 1024)
            self._pos = f.tell()
        data = self._partial + chunk.decode("utf-8", errors="replace")
        lines = data.split("\n")
        self._partial = lines.pop()  # incomplete last line, finish next time
        if skip_partial and lines:
            lines.pop(0)              # we started mid-line
        for line in lines:
            rec = parse_line(line)
            if rec:
                self.stats.ingest(rec)
                n += 1
        return n

    def run(self) -> None:
        while True:
            try:
                while self.poll():
                    pass
            except OSError:
                pass
            time.sleep(self.interval)
