"""lanjaeger web UI server: JSON API + the built Angular app.

    python3 -m lanjaeger_webui.app

Environment:
  WEBUI_PASSWORD          required for sign-in; without it the UI is read-only
  WEBUI_PUBLIC_DASHBOARD  "true" = stats readable without signing in
  PROJECT_DIR             host path of the lanjaeger folder (mounted at the same path)
  LOG_DIR, CACHE_DIR      default /data/logs, /data/cache
  STATIC_DIR              built frontend, default /app/static
  PORT                    default 8080
"""

from __future__ import annotations

import json
import mimetypes
import os
import re
import sys
import threading
import urllib.parse
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import adblock, alerts, checkdns, disk, dnsentries, docker_api, envfile, prefill
from .auth import COOKIE, SESSION_TTL, Auth, csrf_ok
from .jobs import MANAGED, JobRunner
from .stats import LogFollower, Stats

# Steam capsule images come from Steam's CDN (prefill game picker); the
# client check page probes one-time names in the check zone.
CSP = ("default-src 'self'; img-src 'self' data: https://cdn.akamai.steamstatic.com; font-src 'self' data:; style-src 'self' 'unsafe-inline'; "
       f"script-src 'self'; connect-src 'self' http://*.{checkdns.ZONE}:*; "
       "frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
MAX_BODY = 64 * 1024


class Context:
    def __init__(self):
        self.auth = Auth(os.environ.get("WEBUI_PASSWORD", ""))
        self.public = os.environ.get("WEBUI_PUBLIC_DASHBOARD", "").lower() == "true"
        self.project_dir = os.environ.get("PROJECT_DIR", "")
        self.env_path = os.path.join(self.project_dir, ".env") if self.project_dir else ""
        self.log_dir = os.environ.get("LOG_DIR", "/data/logs")
        self.cache_dir = os.environ.get("CACHE_DIR", "/data/cache")
        self.static = Path(os.environ.get("STATIC_DIR", "/app/static")).resolve()
        self.stats = Stats()
        self.cache_size = disk.CacheSize(self.cache_dir)
        self.docker = docker_api.available()
        self.project = docker_api.own_project() if self.docker else None
        self.jobs = JobRunner(self.project_dir, self.project)
        self.prefill = prefill.Runner(self.project_dir, self.project, self.env_path) if self.project_dir else None
        self.logins = {s: prefill.Login(self.prefill, s) for s in prefill.Login.COMMANDS} if self.prefill else {}
        self.alerts = alerts.Alerts(self.project_dir, self.project, self.cache_dir, self.prefill) if self.project_dir else None
        self.checks = checkdns.Store()

    def check_answer_ip(self) -> str | None:
        """Where players reach the web UI: WEBUI_BIND_IP, else DNS_BIND_IP."""
        env = envfile.read(self.env_path) if self.env_path else {}
        ip = env.get("WEBUI_BIND_IP") or ""
        return ip if ip and ip != "0.0.0.0" else env.get("DNS_BIND_IP") or None

    def start_background(self):
        LogFollower(os.path.join(self.log_dir, "access.log"), self.stats).start()
        self.cache_size.start()
        if self.env_path:
            adblock.Reconciler(self.env_path).start()
        if self.prefill and self.docker:
            threading.Thread(target=self.prefill.run_scheduler, daemon=True, name="prefill-schedule").start()
        if self.alerts:
            self.alerts.start()
        try:
            port = int(os.environ.get("CHECK_DNS_PORT", "5353"))
            responder = checkdns.Responder(self.checks, self.check_answer_ip, port)
            responder.bind()
            responder.start()
        except OSError as exc:
            print(f"check DNS responder disabled: {exc}", file=sys.stderr)


class Handler(BaseHTTPRequestHandler):
    ctx: Context
    server_version = "lanjaeger"
    sys_version = ""

    # ── plumbing ──────────────────────────────────────────────────────

    def log_message(self, fmt, *args):
        if os.environ.get("WEBUI_ACCESS_LOG") == "true":
            super().log_message(fmt, *args)

    def _headers(self, status: int, ctype: str, length: int, extra: dict | None = None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()

    def _json(self, status: int, data, extra: dict | None = None):
        body = json.dumps(data).encode()
        self._headers(status, "application/json", len(body), {"Cache-Control": "no-store", **(extra or {})})
        if self.command != "HEAD":
            self.wfile.write(body)

    def _error(self, status: int, message: str, **more):
        self._json(status, {"error": message, **more})

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY:
            raise ValueError("request too large")
        data = json.loads(self.rfile.read(n) or b"{}")
        if not isinstance(data, dict):
            raise ValueError("expected a JSON object")
        return data

    def _token(self) -> str | None:
        c = cookies.SimpleCookie(self.headers.get("Cookie", ""))
        return c[COOKIE].value if COOKIE in c else None

    def _signed_in(self) -> bool:
        return self.ctx.auth.valid(self._token())

    def _client_ip(self) -> str:
        return self.client_address[0]

    # ── routing ───────────────────────────────────────────────────────

    def do_GET(self):
        self._dispatch("GET")

    def do_HEAD(self):
        self._dispatch("HEAD")

    def do_POST(self):
        self._dispatch("POST")

    def do_PUT(self):
        self._dispatch("PUT")

    def _dispatch(self, method: str):
        path = self.path.split("?", 1)[0]
        if not path.startswith("/api/"):
            return self._static(path) if method in ("GET", "HEAD") else self._error(405, "method not allowed")
        if not csrf_ok(method, self.headers):
            return self._error(403, "cross-site request refused")
        routes = [
            ("GET", r"/api/session", self.session, "none"),
            ("POST", r"/api/check/start", self.post_check_start, "none"),
            ("GET", r"/api/check/result/([0-9a-f]{16})", self.get_check_result, "none"),
            ("GET", r"/api/check/ping", self.get_check_ping, "none"),
            ("POST", r"/api/login", self.login, "none"),
            ("POST", r"/api/logout", self.logout, "none"),
            ("GET", r"/api/stats", self.get_stats, "read"),
            ("GET", r"/api/disk", self.get_disk, "read"),
            ("GET", r"/api/services", self.get_services, "read"),
            ("POST", r"/api/services/([a-z0-9_-]+)/restart", self.restart, "admin"),
            ("GET", r"/api/services/([a-z0-9_-]+)/logs", self.get_logs, "admin"),
            ("GET", r"/api/config", self.get_config, "admin"),
            ("PUT", r"/api/config", self.put_config, "admin"),
            ("POST", r"/api/jobs", self.post_job, "admin"),
            ("GET", r"/api/jobs/current", self.get_job, "admin"),
            ("GET", r"/api/adblock", self.get_adblock, "read"),
            ("POST", r"/api/adblock/blocking", self.post_blocking, "admin"),
            ("GET", r"/api/adblock/lists", self.get_lists, "admin"),
            ("PUT", r"/api/adblock/lists/(allow|deny)", self.put_list, "admin"),
            ("GET", r"/api/dns/entries", self.get_dns_entries, "admin"),
            ("PUT", r"/api/dns/entries", self.put_dns_entries, "admin"),
            ("POST", r"/api/dns/lookup", self.post_dns_lookup, "admin"),
            ("POST", r"/api/dns/apply", self.post_dns_apply, "admin"),
            ("GET", r"/api/alerts", self.get_alerts, "admin"),
            ("PUT", r"/api/alerts/settings", self.put_alerts, "admin"),
            ("POST", r"/api/alerts/test", self.post_alerts_test, "admin"),
            ("GET", r"/api/alerts/active", self.get_alerts_active, "read"),
            ("GET", r"/api/prefill", self.get_prefill, "admin"),
            ("PUT", r"/api/prefill/settings", self.put_prefill_settings, "admin"),
            ("POST", r"/api/prefill/run", self.post_prefill_run, "admin"),
            ("POST", r"/api/prefill/stop", self.post_prefill_stop, "admin"),
            ("GET", r"/api/prefill/steam/search", self.get_steam_search, "admin"),
            ("GET", r"/api/prefill/(steam|epic)/login", self.get_login, "admin"),
            ("POST", r"/api/prefill/(steam|epic)/login", self.post_login, "admin"),
            ("POST", r"/api/prefill/(steam|epic)/login/input", self.post_login_input, "admin"),
            ("POST", r"/api/prefill/(steam|epic)/logout", self.post_logout, "admin"),
            ("GET", r"/api/prefill/epic/library", self.get_epic_library, "admin"),
            ("POST", r"/api/prefill/epic/library/refresh", self.post_epic_refresh, "admin"),
        ]
        for m, pattern, fn, need in routes:
            match = re.fullmatch(pattern, path)
            if match and (m == method or (m == "GET" and method == "HEAD")):
                if need == "admin" and not self._signed_in():
                    return self._error(401, "sign in required")
                if need == "read" and not (self.ctx.public or self._signed_in()):
                    return self._error(401, "sign in required")
                try:
                    return fn(*match.groups())
                except (ValueError, json.JSONDecodeError) as exc:
                    return self._error(400, str(exc))
                except (docker_api.DockerError, adblock.BlockyError) as exc:
                    return self._error(502, str(exc))
        self._error(404, "not found")

    # ── static files (Angular build, SPA fallback) ────────────────────

    def _static(self, path: str):
        rel = path.lstrip("/") or "index.html"
        target = (self.ctx.static / rel).resolve()
        if not target.is_relative_to(self.ctx.static) or not target.is_file():
            target = self.ctx.static / "index.html"  # client-side route
        if not target.is_file():
            return self._error(404, "frontend not built")
        data = target.read_bytes()
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        # Angular emits hashed file names: cache them forever, never index.html.
        hashed = re.search(r"-[A-Z0-9]{8,}\.(js|css)$", target.name)
        cache = "public, max-age=31536000, immutable" if hashed else "no-cache"
        self._headers(200, ctype, len(data), {"Cache-Control": cache})
        if self.command != "HEAD":
            self.wfile.write(data)

    # ── API ───────────────────────────────────────────────────────────

    def session(self):
        signed_in = self._signed_in()
        self._json(200, {
            "signedIn": signed_in,
            "passwordSet": self.ctx.auth.enabled,
            "publicDashboard": self.ctx.public,
            "controls": self.ctx.docker and bool(self.ctx.project_dir),
            "project": self.ctx.project,
        })

    def login(self):
        body = self._body()
        ip = self._client_ip()
        if not self.ctx.auth.enabled:
            return self._error(403, "WEBUI_PASSWORD is not set — sign-in is disabled")
        wait = self.ctx.auth.locked_out(ip)
        if wait:
            return self._error(429, "too many attempts", retryAfter=int(wait) + 1)
        token = self.ctx.auth.login(ip, str(body.get("password", "")))
        if not token:
            return self._error(401, "wrong password")
        c = f"{COOKIE}={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={SESSION_TTL}"
        self._json(200, {"signedIn": True}, {"Set-Cookie": c})

    def logout(self):
        self.ctx.auth.logout(self._token())
        self._json(200, {"signedIn": False}, {"Set-Cookie": f"{COOKIE}=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0"})

    def get_stats(self):
        self._json(200, self.ctx.stats.snapshot())

    def get_disk(self):
        limit = envfile.read(self.ctx.env_path).get("CACHE_DISK_SIZE") if self.ctx.env_path else None
        self._json(200, disk.usage(self.ctx.cache_dir, self.ctx.cache_size,
                                   limit or os.environ.get("CACHE_DISK_SIZE")))

    def get_services(self):
        if not self.ctx.docker or not self.ctx.project:
            return self._json(200, {"available": False, "services": []})
        self._json(200, {"available": True, "services": docker_api.containers(self.ctx.project),
                         "managed": MANAGED})

    def restart(self, service):
        if service not in MANAGED:
            raise ValueError(f"{service} can't be restarted from here")
        docker_api.restart(self.ctx.project, service)
        self._json(200, {"restarted": service})

    def get_logs(self, service):
        self._json(200, {"service": service, "text": docker_api.logs(self.ctx.project, service, 300)})

    def get_config(self):
        if not self.ctx.env_path or not os.path.exists(self.ctx.env_path):
            return self._error(404, ".env not found — is PROJECT_DIR mounted?")
        self._json(200, {"fields": envfile.editable(self.ctx.env_path),
                         "warnings": envfile.warnings(envfile.read(self.ctx.env_path))})

    def put_config(self):
        changes = self._body().get("values")
        if not isinstance(changes, dict):
            raise ValueError("expected {\"values\": {...}}")
        clean, errors = envfile.validate(changes)
        if errors:
            return self._error(422, "invalid values", fields=errors)
        changed = envfile.write(self.ctx.env_path, clean)
        self._json(200, {"changed": changed, "warnings": envfile.warnings(envfile.read(self.ctx.env_path))})

    # ── client check (public) ─────────────────────────────────────────

    def post_check_start(self):
        check = self.ctx.checks.new_check(self._client_ip())
        self._json(200, {"id": check["id"], "hosts": [f"{n}.{checkdns.ZONE}" for n in check["nonces"]]})

    def get_check_result(self, check_id):
        r = self.ctx.checks.result(check_id)
        if r is None:
            return self._error(404, "unknown or expired check")
        env = envfile.read(self.ctx.env_path) if self.ctx.env_path else {}
        self._json(200, {**r, "clientIsIpv6": ":" in r["client"],
                         "dnsServer": env.get("DNS_BIND_IP"), "dnsServer6": env.get("DNS_BIND_IP6") or None})

    def get_check_ping(self):
        # Reached via a probe name; lets the page's fetch succeed cross-origin.
        self._headers(204, "text/plain", 0, {"Access-Control-Allow-Origin": "*", "Cache-Control": "no-store"})

    # ── ad blocking ───────────────────────────────────────────────────

    def get_adblock(self):
        desired = adblock.desired_enabled(self.ctx.env_path) if self.ctx.env_path else True
        try:
            st = adblock.status()
            stats = adblock.stats()
        except adblock.BlockyError as exc:
            return self._json(200, {"available": False, "error": str(exc), "persistedEnabled": desired})
        self._json(200, {
            "available": True,
            "active": adblock.ads_active(st),
            "pausedForSeconds": st.get("autoEnableInSec") or 0,
            "persistedEnabled": desired,
            "summary": stats.get("summary", {}),
            "perHour": stats.get("perHour", []),
            "topBlockedDomains": stats.get("topBlockedDomains", [])[:20],
            "topClients": stats.get("topClients", [])[:20],
            "lists": stats.get("lists", {}),
        })

    def post_blocking(self):
        body = self._body()
        enabled = body.get("enabled")
        minutes = body.get("minutes")
        if not isinstance(enabled, bool):
            raise ValueError("expected {\"enabled\": true|false}")
        if minutes is not None:
            if enabled or not isinstance(minutes, int) or not 1 <= minutes <= 1440:
                raise ValueError("a pause needs enabled=false and minutes between 1 and 1440")
            adblock.disable(minutes)   # temporary: not persisted
        else:
            adblock.set_desired(self.ctx.env_path, enabled)
            adblock.enable() if enabled else adblock.disable()
        self._json(200, {"enabled": enabled, "minutes": minutes})

    def get_lists(self):
        d = self.ctx.project_dir
        self._json(200, {"allow": adblock.read_list(d, "allow"), "deny": adblock.read_list(d, "deny")})

    def put_list(self, kind):
        domains, invalid = adblock.normalize(self._body().get("domains"))
        if invalid:
            return self._error(422, "invalid domains", invalid=invalid)
        adblock.write_list(self.ctx.project_dir, kind, domains)
        refreshed = True
        try:
            adblock.refresh_lists()
        except adblock.BlockyError:
            refreshed = False  # saved; Blocky picks it up on its next refresh/restart
        self._json(200, {"domains": domains, "refreshed": refreshed})

    # ── alerts ────────────────────────────────────────────────────────

    def _alerts(self) -> alerts.Alerts:
        if not self.ctx.alerts:
            raise ValueError("alerts need PROJECT_DIR")
        return self.ctx.alerts

    def get_alerts(self):
        self._json(200, self._alerts().snapshot())

    def get_alerts_active(self):
        # Titles only: shown as a banner on the dashboard (which may be public).
        a = self.ctx.alerts.snapshot()["active"] if self.ctx.alerts else []
        self._json(200, {"active": [{"title": x["title"], "severity": x["severity"]} for x in a]})

    def put_alerts(self):
        clean = self._alerts().save(self._body().get("settings"))
        self._json(200, {"settings": alerts.public(clean)})

    def post_alerts_test(self):
        results = self._alerts().test()
        if not results:
            return self._error(400, "no channel is switched on")
        self._json(200, {"results": results})

    # ── prefill ───────────────────────────────────────────────────────

    def _prefill(self) -> prefill.Runner:
        if not self.ctx.prefill or not self.ctx.docker:
            raise docker_api.DockerError("prefill needs the Docker socket and PROJECT_DIR")
        return self.ctx.prefill

    def get_prefill(self):
        p = self._prefill()
        settings = p.store.load()
        self._json(200, {
            "settings": settings,
            "loggedIn": {s: p.store.logged_in(s) for s in prefill.SERVICE_ORDER},
            "runnable": p.runnable(settings),
            "battlenetProducts": prefill.BATTLENET_PRODUCTS,
            "status": p.status(),
        })

    def put_prefill_settings(self):
        clean = self._prefill().store.save(self._body().get("settings"))
        self._json(200, {"settings": clean})

    def post_prefill_run(self):
        services = self._body().get("services") or ["steam", "battlenet"]
        if not isinstance(services, list):
            raise ValueError("services must be a list")
        try:
            started = self._prefill().start([str(s) for s in services])
        except RuntimeError as exc:
            return self._error(409, str(exc))
        self._json(202, {"started": started})

    def post_prefill_stop(self):
        self._prefill().stop()
        self._json(200, {"stopped": True})

    def get_steam_search(self):
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("q", [""])[0]
        try:
            self._json(200, {"results": prefill.steam_search(q)})
        except OSError as exc:
            self._error(502, f"Steam store search failed: {exc}")

    def get_login(self, service):
        self._prefill()
        self._json(200, self.ctx.logins[service].status())

    def post_login(self, service):
        self._prefill()
        try:
            self.ctx.logins[service].start()
        except RuntimeError as exc:
            return self._error(409, str(exc))
        self._json(202, self.ctx.logins[service].status())

    def post_login_input(self, service):
        body = self._body()
        try:
            self.ctx.logins[service].send(str(body.get("text", "")), bool(body.get("secret")))
        except RuntimeError as exc:
            return self._error(409, str(exc))
        self._json(200, {"sent": True})

    def post_logout(self, service):
        self._prefill().store.logout(service)
        self._json(200, {"loggedIn": False})

    def get_epic_library(self):
        p = self._prefill()
        self._json(200, {**prefill.read_epic_library(p.store), "refresh": p.epic_refresh})

    def post_epic_refresh(self):
        try:
            self._prefill().refresh_epic_library()
        except RuntimeError as exc:
            return self._error(409, str(exc))
        self._json(202, {"refresh": self.ctx.prefill.epic_refresh})

    # ── custom DNS entries ────────────────────────────────────────────

    def _project_dir(self) -> str:
        if not self.ctx.project_dir:
            raise ValueError("DNS entries need PROJECT_DIR")
        return self.ctx.project_dir

    def get_dns_entries(self):
        self._json(200, {"entries": dnsentries.read(self._project_dir())})

    def put_dns_entries(self):
        clean, errors = dnsentries.validate(self._body().get("entries"))
        if errors:
            return self._error(422, "invalid entries", rows={str(k): v for k, v in errors.items()})
        dnsentries.write(self._project_dir(), clean)
        self._json(200, {"entries": clean})

    def post_dns_lookup(self):
        body = self._body()
        name = str(body.get("name", "")).strip().lower().rstrip(".")
        qtype = str(body.get("type", "A")).upper()
        if not dnsentries.NAME.fullmatch(name) or qtype not in ("A", "AAAA", "CNAME", "TXT", "MX"):
            raise ValueError("enter a hostname and a type of A, AAAA, CNAME, TXT or MX")
        self._json(200, adblock.query(name, qtype))

    def post_dns_apply(self):
        # Blocky re-reads hosts.txt every minute; a restart applies it now
        # (a ~2 s DNS gap for the LAN, and the 24 h ad-block stats reset).
        if not self.ctx.docker or not self.ctx.project:
            raise ValueError("applying immediately needs the Docker socket")
        docker_api.restart(self.ctx.project, "blocky")
        self._json(200, {"restarted": "blocky"})

    def post_job(self):
        action = self._body().get("action")
        try:
            job = self.ctx.jobs.start(str(action))
        except RuntimeError as exc:
            return self._error(409, str(exc))
        self._json(202, job)

    def get_job(self):
        self._json(200, {"job": self.ctx.jobs.status()})


def main():
    ctx = Context()
    if not ctx.auth.enabled:
        print("WEBUI_PASSWORD not set: sign-in and controls are disabled.", file=sys.stderr)
    ctx.start_background()
    Handler.ctx = ctx
    port = int(os.environ.get("PORT", "8080"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    server.daemon_threads = True
    print(f"lanjaeger web UI on :{port} (project={ctx.project}, controls={'on' if ctx.docker else 'off'})")
    server.serve_forever()


if __name__ == "__main__":
    main()
