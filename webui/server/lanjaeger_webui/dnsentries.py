"""Custom DNS entries, stored as Blocky's hosts file (blocky/hosts.txt).

Blocky re-reads the file every minute (hostsFile.loading.refreshPeriod), so
saving needs no DNS restart. Format per line: `<IP> <name> [# note]`.
Like the ad-block lists, the file is bind-mounted into Blocky by inode, so
it is rewritten in place rather than replaced.
"""

from __future__ import annotations

import ipaddress
import os
import re

FILE = "hosts.txt"
MAX_ENTRIES = 1000

# Lowercase labels, optional dots; single-label names (e.g. "nas") allowed.
NAME = re.compile(r"(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*")


def path(project_dir: str) -> str:
    return os.path.join(project_dir, "blocky", FILE)


def read(project_dir: str) -> list[dict]:
    entries = []
    try:
        with open(path(project_dir), encoding="utf-8") as f:
            for line in f:
                body, _, note = line.partition("#")
                parts = body.split()
                if len(parts) < 2:
                    continue
                ip, names = parts[0], parts[1:]
                for name in names:
                    entries.append({"name": name, "ip": ip, "note": note.strip()})
    except FileNotFoundError:
        pass
    return entries


def validate(entries) -> tuple[list[dict], dict[int, str]]:
    """Returns (clean entries, errors by row index)."""
    if not isinstance(entries, list) or len(entries) > MAX_ENTRIES:
        raise ValueError(f"expected a list of at most {MAX_ENTRIES} entries")
    clean, errors, seen = [], {}, set()
    for i, e in enumerate(entries):
        if not isinstance(e, dict):
            errors[i] = "invalid entry"
            continue
        name = str(e.get("name", "")).strip().lower().rstrip(".")
        ip = str(e.get("ip", "")).strip()
        note = re.sub(r"[\r\n#]", " ", str(e.get("note", ""))).strip()[:100]
        if not NAME.fullmatch(name):
            errors[i] = f"{name or '(empty)'} is not a valid hostname"
        elif name.endswith(".local"):
            errors[i] = ".local names belong to mDNS (Bonjour) — use e.g. .lan instead"
        else:
            try:
                addr = ipaddress.ip_address(ip)
                ip = str(addr)
            except ValueError:
                errors[i] = f"{ip or '(empty)'} is not an IP address"
                continue
            if (name, ip) in seen:
                errors[i] = "duplicate entry"
                continue
            seen.add((name, ip))
            clean.append({"name": name, "ip": ip, "note": note})
    return clean, errors


def write(project_dir: str, entries: list[dict]) -> None:
    p = path(project_dir)
    header = []
    try:
        with open(p, encoding="utf-8") as f:
            header = [l for l in f if l.lstrip().startswith("#")]
    except FileNotFoundError:
        header = ["# Custom DNS entries, hosts-file format: <IP> <name> [# note]\n"]
    lines = [f"{e['ip']} {e['name']}" + (f"  # {e['note']}" if e["note"] else "") + "\n" for e in entries]
    with open(p, "r+" if os.path.exists(p) else "w", encoding="utf-8") as f:
        f.seek(0)
        f.writelines(header + lines)
        f.truncate()
