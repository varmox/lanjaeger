"""Disk usage of the cache volume.

Filesystem numbers come from statvfs (instant). The cache's own size needs a
directory walk, which takes minutes on multi-TB caches, so it runs in a
background thread every 30 minutes and the last result is served.
"""

from __future__ import annotations

import os
import re
import threading
import time

RESCAN_S = 1800


def parse_size(s: str | None) -> int | None:
    """'2000g' / '500m' → bytes (nginx units)."""
    m = re.fullmatch(r"(\d+)([kmgt]?)", (s or "").strip().lower())
    if not m:
        return None
    return int(m[1]) * 1024 ** "_kmgt".index(m[2] or "_")


class CacheSize(threading.Thread):
    def __init__(self, path: str):
        super().__init__(daemon=True, name="cache-size")
        self.path = path
        self.bytes: int | None = None
        self.scanned_at: float | None = None

    def scan(self) -> int:
        total = 0
        stack = [self.path]
        while stack:
            try:
                with os.scandir(stack.pop()) as it:
                    for e in it:
                        try:
                            if e.is_dir(follow_symlinks=False):
                                stack.append(e.path)
                            elif e.is_file(follow_symlinks=False):
                                total += e.stat(follow_symlinks=False).st_blocks * 512
                        except OSError:
                            pass
            except OSError:
                pass
        return total

    def run(self) -> None:
        while True:
            self.bytes = self.scan()
            self.scanned_at = time.time()
            time.sleep(RESCAN_S)


def usage(path: str, cache: CacheSize, limit: str | None) -> dict:
    try:
        st = os.statvfs(path)
        fs_total = st.f_blocks * st.f_frsize
        fs_free = st.f_bavail * st.f_frsize
    except OSError:
        fs_total = fs_free = None
    return {
        "filesystemTotal": fs_total,
        "filesystemFree": fs_free,
        "cacheBytes": cache.bytes,
        "cacheScannedAt": cache.scanned_at,
        "cacheLimit": parse_size(limit),
    }
