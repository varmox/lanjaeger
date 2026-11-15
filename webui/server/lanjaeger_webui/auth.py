"""Single-password sign-in with server-side sessions.

- Password from WEBUI_PASSWORD, compared in constant time.
- Session id: 256-bit random, in an HttpOnly SameSite=Strict cookie,
  kept only in memory (a restart signs everyone out — acceptable for a LAN tool).
- Brute force: per-IP attempt limit with lockout.
- CSRF: state-changing requests must carry the X-Lanjaeger header (a custom
  header forces a CORS preflight cross-site, which we never allow) and,
  when the browser sends one, an Origin matching the Host.
"""

from __future__ import annotations

import hmac
import secrets
import threading
import time

COOKIE = "lj_session"
SESSION_TTL = 12 * 3600
MAX_ATTEMPTS = 5
WINDOW_S = 300
LOCKOUT_S = 300


class Auth:
    def __init__(self, password: str):
        self.password = password
        self._sessions: dict[str, float] = {}
        self._attempts: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return bool(self.password)

    def locked_out(self, ip: str, now: float | None = None) -> float:
        """Seconds until this IP may try again (0 = not locked)."""
        now = time.time() if now is None else now
        with self._lock:
            tries = [t for t in self._attempts.get(ip, []) if t > now - WINDOW_S]
            self._attempts[ip] = tries
            if len(tries) >= MAX_ATTEMPTS:
                return max(0.0, tries[-1] + LOCKOUT_S - now)
            return 0.0

    def login(self, ip: str, password: str) -> str | None:
        if not self.enabled or self.locked_out(ip):
            return None
        if hmac.compare_digest(password.encode(), self.password.encode()):
            token = secrets.token_urlsafe(32)
            with self._lock:
                self._sessions[token] = time.time() + SESSION_TTL
                self._attempts.pop(ip, None)
            return token
        with self._lock:
            self._attempts.setdefault(ip, []).append(time.time())
        return None

    def valid(self, token: str | None) -> bool:
        if not token:
            return False
        now = time.time()
        with self._lock:
            exp = self._sessions.get(token)
            if exp is None or exp < now:
                self._sessions.pop(token, None)
                return False
            return True

    def logout(self, token: str | None) -> None:
        with self._lock:
            self._sessions.pop(token or "", None)


def csrf_ok(method: str, headers) -> bool:
    if method in ("GET", "HEAD"):
        return True
    if headers.get("X-Lanjaeger") != "1":
        return False
    origin, host = headers.get("Origin"), headers.get("Host")
    if origin and host:
        return origin.split("://", 1)[-1] == host
    return True
