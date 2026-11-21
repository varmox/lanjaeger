"""DNS path check for the "Is my device using the cache?" page.

A tiny DNS responder (UDP, stdlib) for one private zone only. Blocky forwards
just this zone here (`conditional` in blocky/config.yml), so nothing else a
player looks up is ever seen or recorded.

The browser resolves a few one-time names `<nonce>.check.lanjaeger.internal`.
Each nonce that arrives here proves that lookup went through lanjaeger's DNS:
  all seen  → the device uses our DNS
  some seen → it also uses another DNS server (often the router's IPv6 DNS)
  none seen → it doesn't use our DNS, or the browser uses its own (DoH)
A queries are answered with the web UI's address, so the browser's request
for the name also reaches us.
"""

from __future__ import annotations

import ipaddress
import re
import secrets
import socket
import struct
import threading
import time

ZONE = "check.lanjaeger.internal"
NONCE = re.compile(r"[a-z0-9]{16}")
PROBES = 6
TTL_S = 600
MAX_CHECKS = 2000


class Store:
    """Checks (id → nonces) and which nonces the DNS responder has seen."""

    def __init__(self):
        self._lock = threading.Lock()
        self.checks: dict[str, dict] = {}
        self.seen: dict[str, float] = {}

    def _prune(self, now: float) -> None:
        for k in [k for k, t in self.seen.items() if now - t > TTL_S]:
            del self.seen[k]
        for k in [k for k, c in self.checks.items() if now - c["created"] > TTL_S]:
            del self.checks[k]
        while len(self.checks) > MAX_CHECKS:
            self.checks.pop(next(iter(self.checks)))

    def new_check(self, client_ip: str, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        check = {"id": secrets.token_hex(8), "created": now, "client": client_ip,
                 "nonces": [secrets.token_hex(8) for _ in range(PROBES)]}
        with self._lock:
            self._prune(now)
            self.checks[check["id"]] = check
        return check

    def mark(self, nonce: str, now: float | None = None) -> None:
        with self._lock:
            if len(self.seen) < 50_000:
                self.seen[nonce] = time.time() if now is None else now

    def result(self, check_id: str) -> dict | None:
        with self._lock:
            check = self.checks.get(check_id)
            if not check:
                return None
            seen = [n for n in check["nonces"] if n in self.seen]
        return {"total": len(check["nonces"]), "seen": len(seen), "client": check["client"]}


# ── DNS wire format (just enough for one zone) ──────────────────────────


def parse_query(data: bytes) -> tuple[int, int, str, int, int] | None:
    """Returns (id, flags, qname, qtype, end_of_question) or None."""
    if len(data) < 12:
        return None
    qid, flags, qdcount = struct.unpack(">HHH", data[:6])
    if flags & 0x8000 or qdcount != 1:      # not a query, or not exactly one question
        return None
    labels, i = [], 12
    while True:
        if i >= len(data):
            return None
        n = data[i]
        if n == 0:
            i += 1
            break
        if n & 0xC0 or i + 1 + n > len(data):  # no compression in questions; bounds
            return None
        labels.append(data[i + 1:i + 1 + n].decode("ascii", "replace").lower())
        i += 1 + n
    if i + 4 > len(data):
        return None
    qtype, _qclass = struct.unpack(">HH", data[i:i + 4])
    return qid, flags, ".".join(labels), qtype, i + 4


def build_response(data: bytes, answer_ip: str | None, store: Store) -> bytes | None:
    q = parse_query(data)
    if not q:
        return None
    qid, flags, qname, qtype, qend = q
    question = data[12:qend]
    rd = flags & 0x0100
    in_zone = qname == ZONE or qname.endswith("." + ZONE)
    if in_zone:
        first = qname[: -len(ZONE) - 1] if qname != ZONE else ""
        if NONCE.fullmatch(first):
            store.mark(first)
    answers = b""
    if in_zone and qtype == 1 and answer_ip:          # A
        try:
            ip = ipaddress.IPv4Address(answer_ip).packed
            answers = struct.pack(">HHHIH", 0xC00C, 1, 1, 0, 4) + ip
        except ValueError:
            pass
    rcode = 0 if in_zone else 3                       # NXDOMAIN outside our zone
    hdr_flags = 0x8000 | 0x0400 | rd | rcode          # QR, AA
    header = struct.pack(">HHHHHH", qid, hdr_flags, 1, 1 if answers else 0, 0, 0)
    return header + question + answers


class Responder(threading.Thread):
    def __init__(self, store: Store, answer_ip, port: int = 5353):
        super().__init__(daemon=True, name="check-dns")
        self.store, self.answer_ip, self.port = store, answer_ip, port
        self.sock: socket.socket | None = None

    def bind(self) -> int:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("0.0.0.0", self.port))
        return self.sock.getsockname()[1]

    def run(self) -> None:
        if self.sock is None:
            self.bind()
        while True:
            try:
                data, addr = self.sock.recvfrom(512)
                resp = build_response(data, self.answer_ip(), self.store)
                if resp:
                    self.sock.sendto(resp, addr)
            except OSError:
                time.sleep(0.1)
