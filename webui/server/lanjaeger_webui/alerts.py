"""Disk and health alerts, sent to ntfy, Discord and/or email.

A checker thread evaluates the rules every minute. Each problem is an alert
with a stable key ("disk", "service:monolithic", "prefill:steam", ...):

- it fires once it has been true for its grace period,
- repeats every `repeatHours` while it lasts,
- and sends a "resolved" message when it clears.

Settings (including channel secrets) live in <project>/alerts/settings.json,
which is git-ignored; the API never returns secrets, only whether one is set.
"""

from __future__ import annotations

import json
import os
import re
import smtplib
import ssl
import threading
import time
import urllib.request
from email.message import EmailMessage

from . import adblock, docker_api
from .jobs import MANAGED

SECRET = "__unchanged__"        # placeholder the UI sends back for "keep the stored secret"
SECRET_FIELDS = {("ntfy", "token"), ("discord", "webhook"), ("email", "password")}

DEFAULTS = {
    "enabled": False,
    "repeatHours": 12,
    "rules": {
        "diskPercent": 90,       # filesystem used ≥ this %
        "diskFreeGb": 50,        # or free < this many GB
        "serviceDown": True,
        "serviceDownMinutes": 5,
        "prefillFailed": True,
        "adblockEmpty": True,
    },
    "channels": {
        "ntfy": {"enabled": False, "url": "", "token": ""},
        "discord": {"enabled": False, "webhook": ""},
        "email": {"enabled": False, "host": "", "port": 587, "security": "starttls",
                  "username": "", "password": "", "from": "", "to": ""},
    },
}


def _deep_merge(base: dict, over: dict) -> dict:
    out = json.loads(json.dumps(base))
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        elif k in out:
            out[k] = v
    return out


def _url(v: str, field: str) -> str:
    v = (v or "").strip()
    if v and not re.fullmatch(r"https?://[^\s]+", v):
        raise ValueError(f"{field} must be an http(s) URL")
    return v


def validate(s: dict, current: dict) -> dict:
    """Validate new settings; SECRET placeholders keep the stored value."""
    if not isinstance(s, dict):
        raise ValueError("settings must be an object")
    for ch, key in SECRET_FIELDS:
        given = (s.get("channels") or {}).get(ch) or {}
        if given.get(key) == SECRET:
            given[key] = current["channels"][ch][key]
    m = _deep_merge(DEFAULTS, s)
    r, c = m["rules"], m["channels"]
    for k, lo, hi in (("diskPercent", 50, 99), ("diskFreeGb", 0, 100_000), ("serviceDownMinutes", 1, 1440)):
        if not isinstance(r[k], int) or not lo <= r[k] <= hi:
            raise ValueError(f"rules.{k} must be a whole number {lo}–{hi}")
    if not isinstance(m["repeatHours"], int) or not 1 <= m["repeatHours"] <= 168:
        raise ValueError("repeatHours must be 1–168")
    c["ntfy"]["url"] = _url(c["ntfy"]["url"], "ntfy URL")
    c["discord"]["webhook"] = _url(c["discord"]["webhook"], "Discord webhook")
    if c["discord"]["webhook"] and not re.match(r"https://(discord|discordapp)\.com/api/webhooks/", c["discord"]["webhook"]):
        raise ValueError("Discord webhook must start with https://discord.com/api/webhooks/")
    e = c["email"]
    if e["security"] not in ("starttls", "ssl", "none"):
        raise ValueError("email security must be starttls, ssl or none")
    if not isinstance(e["port"], int) or not 1 <= e["port"] <= 65535:
        raise ValueError("email port must be 1–65535")
    for k in ("from", "to"):
        for addr in filter(None, (a.strip() for a in str(e[k]).split(","))):
            if not re.fullmatch(r"[^@\s,]+@[^@\s,]+\.[^@\s,]+", addr):
                raise ValueError(f"email {k}: {addr!r} is not an email address")
    for name, ch in c.items():
        if ch["enabled"]:
            need = {"ntfy": ["url"], "discord": ["webhook"], "email": ["host", "from", "to"]}[name]
            if any(not str(ch[k]).strip() for k in need):
                raise ValueError(f"{name}: fill in {', '.join(need)} or switch it off")
    m["enabled"] = bool(m["enabled"])
    for ch in c.values():
        ch["enabled"] = bool(ch["enabled"])
    for k in ("serviceDown", "prefillFailed", "adblockEmpty"):
        r[k] = bool(r[k])
    return m


def public(settings: dict) -> dict:
    """Settings for the browser: secrets replaced by a placeholder if set."""
    out = json.loads(json.dumps(settings))
    for ch, key in SECRET_FIELDS:
        out["channels"][ch][key] = SECRET if settings["channels"][ch][key] else ""
    return out


# ── channels ────────────────────────────────────────────────────────────


def _post(url: str, data: bytes, headers: dict) -> None:
    req = urllib.request.Request(url, data=data, headers={"User-Agent": "lanjaeger", **headers}, method="POST")
    with urllib.request.urlopen(req, timeout=15) as r:
        r.read()


def send_ntfy(cfg: dict, title: str, body: str, severity: str) -> None:
    headers = {"Title": title.encode("utf-8").decode("latin-1", "ignore"),
               "Priority": {"critical": "5", "warning": "4", "resolved": "3", "info": "3"}[severity],
               "Tags": {"critical": "rotating_light", "warning": "warning", "resolved": "white_check_mark",
                        "info": "information_source"}[severity]}
    if cfg.get("token"):
        headers["Authorization"] = f"Bearer {cfg['token']}"
    _post(cfg["url"], body.encode(), headers)


def send_discord(cfg: dict, title: str, body: str, severity: str) -> None:
    icon = {"critical": "🚨", "warning": "⚠️", "resolved": "✅", "info": "ℹ️"}[severity]
    payload = {"content": f"{icon} **{title}**\n{body}"[:1900], "allowed_mentions": {"parse": []}}
    _post(cfg["webhook"], json.dumps(payload).encode(), {"Content-Type": "application/json"})


def send_email(cfg: dict, title: str, body: str, severity: str) -> None:
    msg = EmailMessage()
    msg["Subject"] = f"[lanjaeger] {title}"
    msg["From"] = cfg["from"]
    msg["To"] = cfg["to"]
    msg.set_content(body + "\n\n— lanjaeger")
    ctx = ssl.create_default_context()
    if cfg["security"] == "ssl":
        server = smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=20, context=ctx)
    else:
        server = smtplib.SMTP(cfg["host"], cfg["port"], timeout=20)
    with server:
        if cfg["security"] == "starttls":
            server.starttls(context=ctx)
        if cfg["username"]:
            server.login(cfg["username"], cfg["password"])
        server.send_message(msg)


SENDERS = {"ntfy": send_ntfy, "discord": send_discord, "email": send_email}


def notify(settings: dict, title: str, body: str, severity: str) -> dict[str, str]:
    """Send to every enabled channel. Returns {channel: "ok" | error}."""
    results = {}
    for name, ch in settings["channels"].items():
        if not ch.get("enabled"):
            continue
        try:
            SENDERS[name](ch, title, body, severity)
            results[name] = "ok"
        except Exception as exc:  # report every channel's outcome, never crash the checker
            results[name] = f"{type(exc).__name__}: {exc}"[:300]
    return results


# ── checks ──────────────────────────────────────────────────────────────


def gb(n: float) -> str:
    return f"{n / 1024 ** 3:,.0f} GB"


def check_disk(rules: dict, cache_dir: str) -> dict | None:
    try:
        st = os.statvfs(cache_dir)
    except OSError:
        return None
    total, free = st.f_blocks * st.f_frsize, st.f_bavail * st.f_frsize
    if not total:
        return None
    used_pct = 100 * (1 - free / total)
    if used_pct >= rules["diskPercent"] or free < rules["diskFreeGb"] * 1024 ** 3:
        return {"severity": "critical" if used_pct >= 98 else "warning",
                "title": "Cache disk almost full",
                "body": f"The cache disk is {used_pct:.0f}% full ({gb(free)} free of {gb(total)}). "
                        "Lower CACHE_DISK_SIZE or add space — nginx stops caching when the disk is full."}
    return None


def check_services(project: str) -> dict[str, dict]:
    out = {}
    containers = docker_api.containers(project)
    for c in containers:
        if c["service"] not in MANAGED:
            continue
        if c["state"] != "running" or c["health"] == "unhealthy":
            what = "unhealthy" if c["state"] == "running" else c["state"] or "missing"
            out[f"service:{c['service']}"] = {
                "severity": "critical",
                "title": f"{c['service']} is {what}",
                "body": f"Container {c['service']} ({c['image']}): {c['status']}. "
                        "Check its logs in the web UI under Services.",
            }
    present = {c["service"] for c in containers}
    for s in MANAGED:
        if s not in present:
            out[f"service:{s}"] = {"severity": "critical", "title": f"{s} is not running",
                                   "body": f"No container for {s}. Run `docker compose up -d`."}
    return out


def check_prefill(status: dict) -> dict[str, dict]:
    out = {}
    for service, info in (status.get("services") or {}).items():
        run = info.get("lastRun")
        # 130/137/143 = stopped by a signal, i.e. someone pressed Stop: not a failure.
        if run and not run["running"] and run["exitCode"] not in (0, None, 130, 137, 143):
            out[f"prefill:{service}:{run['finishedAt']}"] = {
                "severity": "warning", "oneShot": True,
                "title": f"Prefill failed: {service}",
                "body": f"The {service} prefill run ended with exit code {run['exitCode']}. "
                        "See Prefill → Last runs for the log." + _log_tail(info.get("log", "")),
            }
    return out


def _log_tail(log: str, lines: int = 6) -> str:
    tail = [l for l in log.splitlines() if l.strip()][-lines:]
    return ("\n\n" + "\n".join(tail)) if tail else ""


def check_adblock() -> dict | None:
    try:
        lists = (adblock.stats().get("lists") or {}).get("denylist") or {}
    except adblock.BlockyError:
        return None  # blocky down is reported by the service check
    if "ads" in lists and lists["ads"] == 0:
        return {"severity": "warning", "title": "Ad blocklist is empty",
                "body": "Blocky has 0 entries in its ads list, so nothing is blocked — the list download "
                        "probably failed. It retries on its next refresh; check blocky's logs."}
    return None


# ── state machine ───────────────────────────────────────────────────────


class Alerts(threading.Thread):
    INTERVAL_S = 60

    def __init__(self, project_dir: str, project: str | None, cache_dir: str, prefill_runner=None):
        super().__init__(daemon=True, name="alerts")
        self.dir = os.path.join(project_dir, "alerts")
        self.path = os.path.join(self.dir, "settings.json")
        self.state_path = os.path.join(self.dir, "state.json")
        self.project, self.cache_dir, self.prefill = project, cache_dir, prefill_runner
        self._lock = threading.Lock()
        self.pending: dict[str, float] = {}   # key → first seen
        self.active: dict[str, dict] = {}     # key → alert incl. firedAt, lastSent
        self.history: list[dict] = []
        self.oneshot_sent: set[str] = set()
        self._load_state()

    # persistence
    def load(self) -> dict:
        try:
            with open(self.path, encoding="utf-8") as f:
                return _deep_merge(DEFAULTS, json.load(f))
        except (FileNotFoundError, json.JSONDecodeError):
            return json.loads(json.dumps(DEFAULTS))

    def save(self, new: dict) -> dict:
        clean = validate(new, self.load())
        os.makedirs(self.dir, exist_ok=True)
        tmp = self.path + ".tmp"
        with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as f:
            json.dump(clean, f, indent=1)
        os.replace(tmp, self.path)
        return clean

    def _load_state(self) -> None:
        try:
            with open(self.state_path, encoding="utf-8") as f:
                s = json.load(f)
            self.history = s.get("history", [])[-100:]
            self.oneshot_sent = set(s.get("oneshotSent", [])[-500:])
        except (FileNotFoundError, json.JSONDecodeError):
            pass

    def _save_state(self) -> None:
        os.makedirs(self.dir, exist_ok=True)
        with open(self.state_path, "w", encoding="utf-8") as f:
            json.dump({"history": self.history[-100:], "oneshotSent": sorted(self.oneshot_sent)[-500:]}, f)

    # evaluation
    def collect(self, settings: dict) -> dict[str, dict]:
        r = settings["rules"]
        found: dict[str, dict] = {}
        if (a := check_disk(r, self.cache_dir)):
            found["disk"] = a
        if r["serviceDown"] and self.project and docker_api.available():
            try:
                found.update({k: {**v, "graceMinutes": r["serviceDownMinutes"]}
                              for k, v in check_services(self.project).items()})
            except docker_api.DockerError as exc:
                found["docker"] = {"severity": "critical", "title": "Docker is not reachable",
                                   "body": f"The web UI can't query Docker: {exc}"}
        if r["prefillFailed"] and self.prefill is not None:
            try:
                found.update(check_prefill(self.prefill.status()))
            except docker_api.DockerError:
                pass
        if r["adblockEmpty"] and (a := check_adblock()):
            found["adblock"] = a
        return found

    def evaluate(self, found: dict[str, dict], settings: dict, now: float | None = None) -> list[dict]:
        """Advance the state machine; returns the messages to send."""
        now = time.time() if now is None else now
        out = []
        repeat_s = settings["repeatHours"] * 3600
        with self._lock:
            for key, a in found.items():
                if a.get("oneShot"):
                    if key not in self.oneshot_sent:
                        self.oneshot_sent.add(key)
                        out.append({**a, "key": key, "kind": "fired"})
                    continue
                if key in self.active:
                    if now - self.active[key]["lastSent"] >= repeat_s:
                        self.active[key]["lastSent"] = now
                        out.append({**a, "key": key, "kind": "repeat"})
                    continue
                first = self.pending.setdefault(key, now)
                if now - first >= a.get("graceMinutes", 0) * 60:
                    self.pending.pop(key, None)
                    self.active[key] = {**a, "key": key, "firedAt": now, "lastSent": now}
                    out.append({**a, "key": key, "kind": "fired"})
            for key in [k for k in self.pending if k not in found]:
                del self.pending[key]
            for key in [k for k in self.active if k not in found]:
                a = self.active.pop(key)
                out.append({"key": key, "kind": "resolved", "severity": "resolved",
                            "title": f"Resolved: {a['title']}", "body": "Back to normal."})
        return out

    def dispatch(self, messages: list[dict], settings: dict, now: float | None = None) -> None:
        for m in messages:
            results = notify(settings, m["title"], m["body"], m["severity"]) if settings["enabled"] else {}
            self.history.append({"time": time.time() if now is None else now, "key": m["key"], "kind": m["kind"],
                                 "severity": m["severity"], "title": m["title"], "results": results})
        if messages:
            self.history = self.history[-100:]
            self._save_state()

    def check_once(self) -> None:
        settings = self.load()
        self.dispatch(self.evaluate(self.collect(settings), settings), settings)

    def run(self) -> None:
        time.sleep(30)  # let the stack settle after a restart
        while True:
            try:
                self.check_once()
            except Exception:  # the checker must never die
                pass
            time.sleep(self.INTERVAL_S)

    def test(self) -> dict[str, str]:
        settings = self.load()
        return notify(settings, "Test alert from lanjaeger",
                      "If you can read this, alerts reach you on this channel.", "info")

    def snapshot(self) -> dict:
        with self._lock:
            active = [{k: v for k, v in a.items() if k not in ("graceMinutes",)} for a in self.active.values()]
        return {"settings": public(self.load()), "active": active, "history": list(reversed(self.history[-50:]))}
