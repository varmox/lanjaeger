"""Read and update the stack's .env without disturbing comments or order.

Only the keys in FIELDS can be changed from the browser, each with a strict
validator. Everything else (CACHE_ROOT, images, the web UI's own password)
is deliberately read-only here: changing those from a browser could move
the cache, swap images, or lock you out.
"""

from __future__ import annotations

import ipaddress
import os
import re
import tempfile


def _ipv4(v: str) -> str:
    return str(ipaddress.IPv4Address(v))


def _ip_list(sep_pattern: str):
    def check(v: str) -> str:
        parts = [p for p in re.split(sep_pattern, v.strip()) if p]
        if not parts:
            raise ValueError("at least one IP address is required")
        return [_ipv4(p) for p in parts]
    return check


def _pattern(regex: str, hint: str):
    def check(v: str) -> str:
        if not re.fullmatch(regex, v):
            raise ValueError(hint)
        return v
    return check


def _int_range(lo: int, hi: int):
    def check(v: str) -> str:
        if not re.fullmatch(r"\d+", v) or not lo <= int(v) <= hi:
            raise ValueError(f"whole number between {lo} and {hi}")
        return v
    return check


def _optional_ipv4(v: str) -> str:
    return _ipv4(v) if v else ""


FIELDS: dict[str, dict] = {
    "LANCACHE_IP": {"group": "network", "check": lambda v: " ".join(_ip_list(r"\s+")(v)),
                    "help": "IP(s) clients download from, space-separated"},
    "DNS_BIND_IP": {"group": "network", "check": _ipv4, "help": "IP the DNS server listens on"},
    "CACHE_BIND_IP": {"group": "network", "check": _optional_ipv4,
                      "help": "Bind HTTP/HTTPS to one IP (empty = all)"},
    "UPSTREAM_DNS": {"group": "network", "check": lambda v: "; ".join(_ip_list(r"[;\s]+")(v)),
                     "help": "Resolver(s) for everything not cached"},
    "CACHE_DISK_SIZE": {"group": "storage", "check": _pattern(r"[1-9]\d*g", "gigabytes with g, e.g. 2000g (never t)"),
                        "help": "Maximum cache size — gigabytes with g only"},
    "MIN_FREE_DISK": {"group": "storage", "check": _pattern(r"[1-9]\d*[mg]", "e.g. 10g or 500m"),
                      "help": "Always keep this much free"},
    "CACHE_INDEX_SIZE": {"group": "storage", "check": _pattern(r"[1-9]\d*[mg]", "e.g. 500m"),
                         "help": "About 250m per 1000g of cache"},
    "CACHE_MAX_AGE": {"group": "storage", "check": _pattern(r"[1-9]\d*d", "days with d, e.g. 3650d"),
                      "help": "Maximum age of cached content"},
    "LOG_MAX_SIZE_MB": {"group": "logs", "check": _int_range(10, 1_000_000), "help": "Rotate logs above this size (MB)"},
    "LOG_KEEP": {"group": "logs", "check": _int_range(1, 100), "help": "Rotated copies to keep"},
    "LOG_CHECK_INTERVAL": {"group": "logs", "check": _int_range(60, 86_400), "help": "Seconds between size checks"},
    "RESTART_POLICY": {"group": "operation", "check": _pattern(r"no|always|unless-stopped|on-failure",
                                                                "no, always, unless-stopped or on-failure"),
                       "help": "unless-stopped = start again after a reboot",
                       "options": ["no", "unless-stopped", "always", "on-failure"]},
    "TZ": {"group": "operation", "check": _pattern(r"[A-Za-z_]+(/[A-Za-z0-9_+\-]+){0,2}", "tz name, e.g. Europe/Zurich"),
           "help": "Timezone for log timestamps"},
}

_LINE = re.compile(r"^\s*([A-Z][A-Z0-9_]*)\s*=(.*)$")


def _unquote(v: str) -> str:
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    return v


def read(path: str) -> dict[str, str]:
    values = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                m = _LINE.match(line)
                if m:
                    values[m[1]] = _unquote(m[2])
    except FileNotFoundError:
        pass
    return values


def editable(path: str) -> list[dict]:
    values = read(path)
    return [
        {"key": k, "value": values.get(k, ""), "group": f["group"], "help": f["help"],
         **({"options": f["options"]} if "options" in f else {})}
        for k, f in FIELDS.items()
    ]


def validate(changes: dict) -> tuple[dict[str, str], dict[str, str]]:
    """Returns (clean values, errors by key)."""
    clean, errors = {}, {}
    for key, raw in changes.items():
        if key not in FIELDS:
            errors[key] = "not editable from the web UI"
            continue
        if not isinstance(raw, str) or any(c in raw for c in "\n\r#\"'`$\\"):
            errors[key] = "contains characters that aren't allowed"
            continue
        try:
            clean[key] = FIELDS[key]["check"](raw.strip())
        except ValueError as exc:
            errors[key] = str(exc) if str(exc) else "invalid value"
    return clean, errors


def write(path: str, clean: dict[str, str]) -> list[str]:
    """Apply validated values in place; append keys the file doesn't have yet.
    Atomic replace, keeping the file's permissions. Returns changed keys."""
    with open(path, encoding="utf-8") as f:
        lines = f.readlines()
    current = read(path)
    changed = [k for k, v in clean.items() if current.get(k, "") != v]
    seen = set()
    for i, line in enumerate(lines):
        m = _LINE.match(line)
        if m and m[1] in clean:
            lines[i] = f"{m[1]}={clean[m[1]]}\n"
            seen.add(m[1])
    for k, v in clean.items():
        if k not in seen:
            if lines and not lines[-1].endswith("\n"):
                lines[-1] += "\n"
            lines.append(f"{k}={v}\n")
    mode = os.stat(path).st_mode & 0o777
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", prefix=".env.")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.writelines(lines)
    os.chmod(tmp, mode)
    os.replace(tmp, path)
    return changed
