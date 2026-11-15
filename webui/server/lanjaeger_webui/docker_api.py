"""Minimal Docker Engine API client over the unix socket (stdlib only).

Only what the web UI needs: list this compose project's containers,
restart one, read its recent logs. Image updates and config changes go
through `docker compose` (see jobs.py) so compose stays the single source
of truth for how containers are created.
"""

from __future__ import annotations

import http.client
import json
import os
import socket
import struct
from urllib.parse import quote, urlencode

SOCKET = os.environ.get("DOCKER_SOCK", "/var/run/docker.sock")
PROJECT_LABEL = "com.docker.compose.project"
SERVICE_LABEL = "com.docker.compose.service"


class DockerError(RuntimeError):
    pass


class _UnixConnection(http.client.HTTPConnection):
    def __init__(self, path: str, timeout: float = 30):
        super().__init__("docker", timeout=timeout)
        self._path = path

    def connect(self):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(self.timeout)
        s.connect(self._path)
        self.sock = s


def available() -> bool:
    return os.path.exists(SOCKET)


def _request(method: str, path: str, timeout: float = 30, body: dict | None = None) -> tuple[int, bytes]:
    conn = _UnixConnection(SOCKET, timeout)
    headers = {"Host": "docker"}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    try:
        conn.request(method, path, body=data, headers=headers)
        resp = conn.getresponse()
        return resp.status, resp.read()
    except OSError as exc:
        raise DockerError(f"Docker socket unavailable: {exc}") from None
    finally:
        conn.close()


def _json(method: str, path: str, timeout: float = 30, body: dict | None = None):
    status, raw = _request(method, path, timeout, body)
    if status >= 400:
        raise DockerError(f"Docker API {status}: {raw[:200].decode(errors='replace')}")
    return json.loads(raw) if raw else None


# ── one-off containers (used by prefill) ────────────────────────────────


def image_exists(ref: str) -> bool:
    status, _ = _request("GET", f"/images/{quote(ref, safe='')}/json")
    return status == 200


def pull(ref: str) -> None:
    name, _, tag = ref.rpartition(":")
    status, raw = _request("POST", "/images/create?" + urlencode({"fromImage": name, "tag": tag}), timeout=900)
    # The pull streams JSON progress lines; an error shows up as {"error": ...}.
    if status >= 400 or b'"error"' in raw:
        raise DockerError(f"Pulling {ref} failed: {raw[-300:].decode(errors='replace')}")


def inspect(name: str) -> dict | None:
    status, raw = _request("GET", f"/containers/{quote(name)}/json")
    if status == 404:
        return None
    if status >= 400:
        raise DockerError(f"Docker API {status}: {raw[:200].decode(errors='replace')}")
    return json.loads(raw)


def remove(name: str) -> None:
    status, raw = _request("DELETE", f"/containers/{quote(name)}?force=1")
    if status not in (204, 404):
        raise DockerError(f"Removing {name} failed ({status})")


def create(name: str, config: dict) -> str:
    return _json("POST", "/containers/create?" + urlencode({"name": name}), body=config)["Id"]


def start(cid: str) -> None:
    status, raw = _request("POST", f"/containers/{cid}/start")
    if status not in (204, 304):
        raise DockerError(f"Start failed ({status}): {raw[:200].decode(errors='replace')}")


def stop(name: str) -> None:
    status, _ = _request("POST", f"/containers/{quote(name)}/stop?t=10", timeout=60)
    if status not in (204, 304, 404):
        raise DockerError(f"Stop failed ({status})")


def container_logs(name: str, tail: int = 300) -> str:
    q = urlencode({"stdout": 1, "stderr": 1, "tail": tail})
    status, raw = _request("GET", f"/containers/{quote(name)}/logs?{q}")
    return demux(raw) if status == 200 else ""


def attach(cid: str) -> tuple[socket.socket, bytes]:
    """Hijacked attach (stdin+stdout) to a TTY container. Returns the raw
    socket and any output that arrived with the response headers."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(SOCKET)
    s.sendall((f"POST /containers/{cid}/attach?stream=1&stdin=1&stdout=1&stderr=1 HTTP/1.1\r\n"
               "Host: docker\r\nConnection: Upgrade\r\nUpgrade: tcp\r\n\r\n").encode())
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = s.recv(4096)
        if not chunk:
            raise DockerError("attach: connection closed")
        buf += chunk
    head, rest = buf.split(b"\r\n\r\n", 1)
    code = int(head.split(b" ", 2)[1])
    if code not in (101, 200):
        s.close()
        raise DockerError(f"attach failed ({code})")
    return s, rest


def own_project() -> str | None:
    """Compose project of the container we run in (hostname = container id)."""
    try:
        info = _json("GET", f"/containers/{quote(socket.gethostname())}/json")
        return info["Config"]["Labels"].get(PROJECT_LABEL)
    except (DockerError, KeyError, TypeError):
        return os.environ.get("COMPOSE_PROJECT_NAME")


def containers(project: str) -> list[dict]:
    flt = json.dumps({"label": [f"{PROJECT_LABEL}={project}"]})
    items = _json("GET", "/containers/json?" + urlencode({"all": "1", "filters": flt}))
    out = []
    for c in items:
        labels = c.get("Labels") or {}
        status = c.get("Status", "")
        health = None
        for h in ("healthy", "unhealthy", "health: starting"):
            if f"({h})" in status:
                health = h.replace("health: ", "")
        out.append({
            "id": c["Id"][:12],
            "service": labels.get(SERVICE_LABEL, c["Names"][0].lstrip("/")),
            "image": c.get("Image", ""),
            "state": c.get("State", ""),
            "status": status,
            "health": health,
            "created": c.get("Created"),
        })
    return sorted(out, key=lambda c: c["service"])


def _find(project: str, service: str) -> dict:
    for c in containers(project):
        if c["service"] == service:
            return c
    raise DockerError(f"No container for service {service!r}")


def restart(project: str, service: str) -> None:
    c = _find(project, service)
    status, body = _request("POST", f"/containers/{c['id']}/restart?t=10", timeout=60)
    if status >= 400:
        raise DockerError(f"Restart failed ({status}): {body[:200].decode(errors='replace')}")


def logs(project: str, service: str, tail: int = 200) -> str:
    c = _find(project, service)
    q = urlencode({"stdout": 1, "stderr": 1, "tail": max(1, min(tail, 2000)), "timestamps": 1})
    status, body = _request("GET", f"/containers/{c['id']}/logs?{q}")
    if status >= 400:
        raise DockerError(f"Logs failed ({status})")
    return demux(body)


def demux(raw: bytes) -> str:
    """Strip Docker's 8-byte stream headers (non-TTY containers)."""
    out, i = [], 0
    while i + 8 <= len(raw) and raw[i] in (0, 1, 2) and raw[i + 1:i + 4] == b"\0\0\0":
        (size,) = struct.unpack(">I", raw[i + 4:i + 8])
        out.append(raw[i + 8:i + 8 + size])
        i += 8 + size
    if i < len(raw):  # TTY container or unexpected format: keep the rest as is
        out.append(raw[i:])
    return b"".join(out).decode("utf-8", errors="replace")
