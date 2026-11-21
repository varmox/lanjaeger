"""Ad blocking: talks to Blocky's API and edits its allow/deny lists.

Blocky's API has no authentication, so it is only reachable on the compose
network; the web UI is the authenticated front for it.

Blocking on/off is persisted as ADBLOCK_ENABLED in .env. Blocky always
starts with blocking on (it has no state), so a reconcile loop re-applies
"off" after a Blocky restart. A timed pause is deliberately not persisted.
Only the "ads" group is ever toggled: the DoH/Private Relay canaries stay on.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.error
import urllib.request

from . import envfile

BLOCKY_URL = os.environ.get("BLOCKY_URL", "http://blocky:4000")
GROUP = "ads"
LISTS = {"allow": "allowlist.txt", "deny": "denylist.txt"}
MAX_ENTRIES = 5000

DOMAIN = re.compile(r"(\*\.)?([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+(xn--[a-z0-9-]{1,59}|[a-z]{2,63})")


class BlockyError(RuntimeError):
    pass


def _call(method: str, path: str, timeout: float = 5, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if body is not None else {}
    req = urllib.request.Request(BLOCKY_URL + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
            return json.loads(body) if body and r.headers.get_content_type() == "application/json" else None
    except urllib.error.HTTPError as e:
        raise BlockyError(f"Blocky API {e.code} on {path}") from None
    except (urllib.error.URLError, OSError) as e:
        raise BlockyError(f"Blocky not reachable: {getattr(e, 'reason', e)}") from None


def status() -> dict:
    return _call("GET", "/api/blocking/status")


def stats() -> dict:
    return _call("GET", "/api/stats")


def ads_active(st: dict) -> bool:
    return bool(st.get("enabled")) and GROUP not in (st.get("disabledGroups") or [])


def enable() -> None:
    _call("GET", "/api/blocking/enable")


def disable(minutes: int | None = None) -> None:
    q = f"?groups={GROUP}" + (f"&duration={int(minutes)}m" if minutes else "")
    _call("GET", "/api/blocking/disable" + q)


def refresh_lists() -> None:
    _call("POST", "/api/lists/refresh", timeout=120)


def query(name: str, qtype: str = "A") -> dict:
    """Resolve through Blocky: {reason, response, responseType, returnCode}."""
    return _call("POST", "/api/query", body={"query": name, "type": qtype})


# ── list files ──────────────────────────────────────────────────────────


def normalize(entries: list) -> tuple[list[str], list[str]]:
    """Returns (valid sorted unique domains, invalid inputs)."""
    good, bad = set(), []
    if not isinstance(entries, list) or len(entries) > MAX_ENTRIES:
        raise ValueError(f"expected a list of at most {MAX_ENTRIES} domains")
    for raw in entries:
        d = str(raw).strip().lower().rstrip(".")
        if d.startswith("http://") or d.startswith("https://"):
            d = re.sub(r"^https?://", "", d).split("/", 1)[0]
        if d and len(d) <= 253 and DOMAIN.fullmatch(d):
            good.add(d)
        elif d:
            bad.append(str(raw)[:100])
    return sorted(good), bad


def read_list(project_dir: str, kind: str) -> list[str]:
    path = os.path.join(project_dir, "blocky", LISTS[kind])
    try:
        with open(path, encoding="utf-8") as f:
            return [l.strip() for l in f if l.strip() and not l.lstrip().startswith("#")]
    except FileNotFoundError:
        return []


def write_list(project_dir: str, kind: str, domains: list[str]) -> None:
    """Rewrite in place: the file is bind-mounted into Blocky by inode, so a
    rename-based atomic write would leave Blocky reading the old file."""
    path = os.path.join(project_dir, "blocky", LISTS[kind])
    header = []
    try:
        with open(path, encoding="utf-8") as f:
            header = [l for l in f if l.lstrip().startswith("#")]
    except FileNotFoundError:
        pass
    with open(path, "r+" if os.path.exists(path) else "w", encoding="utf-8") as f:
        f.seek(0)
        f.writelines(header)
        f.writelines(d + "\n" for d in domains)
        f.truncate()


# ── persisted on/off ────────────────────────────────────────────────────


def desired_enabled(env_path: str) -> bool:
    return envfile.read(env_path).get("ADBLOCK_ENABLED", "true").lower() != "false"


def set_desired(env_path: str, enabled: bool) -> None:
    envfile.write(env_path, {"ADBLOCK_ENABLED": "true" if enabled else "false"})


class Reconciler(threading.Thread):
    """Re-applies ADBLOCK_ENABLED=false after Blocky restarts."""

    def __init__(self, env_path: str, interval: float = 60):
        super().__init__(daemon=True, name="adblock-reconcile")
        self.env_path, self.interval = env_path, interval

    def step(self) -> bool:
        """One check. Returns True if it had to switch blocking off again."""
        if self.env_path and not desired_enabled(self.env_path) and ads_active(status()):
            disable()
            return True
        return False

    def run(self) -> None:
        while True:
            try:
                self.step()
            except (BlockyError, OSError):
                pass
            time.sleep(self.interval)
