"""Find newer versions of the container images lanjaeger pins, and update them.

    python3 update-images.py [--apply] [--min-age-days 7]

Looks at the image defaults in docker-compose.yml (`${X_IMAGE:-repo:tag}`)
and the prefill images in webui/server/lanjaeger_webui/prefill.py. For each
image pinned to a version tag, it lists the registry's tags and picks the
highest stable version with the same shape (v1.2.3 stays v1.2.3, 1.34 stays
1.34) that is at least --min-age-days old. Images on :latest are skipped.

Prints a Markdown summary (used as the pull request body); --apply also
rewrites the files. Exit code 0 always; "no updates" is printed when nothing
changed.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

FILES = ["docker-compose.yml", "webui/server/lanjaeger_webui/prefill.py", ".github/workflows/ci.yml"]
# `${X_IMAGE:-repo:tag}` in compose, "repo:tag" in prefill.py, `docker run … repo:tag` in CI.
IMAGE_REF = re.compile(r"""(?:(?<=:-)|(?<=["\s]))(?P<repo>(?:[a-z0-9.-]+\.[a-z]{2,}/)?[a-z][a-z0-9._/-]*):(?P<tag>v?\d+(?:\.\d+){0,3})(?=[}"\s])""")
STABLE = re.compile(r"^(v?)(\d+(?:\.\d+){0,3})$")
ACCEPT = ", ".join([
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
])


def split_repo(repo: str) -> tuple[str, str]:
    """('registry host', 'path') with Docker Hub's defaults."""
    first = repo.split("/", 1)[0]
    if "." in first and "/" in repo:
        return first, repo.split("/", 1)[1]
    host = "registry-1.docker.io"
    return host, repo if "/" in repo else f"library/{repo}"


class Registry:
    def __init__(self, host: str, path: str):
        self.host, self.path, self.token = host, path, None

    def _get(self, url: str, accept: str | None = None):
        headers = {"Accept": accept} if accept else {}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        req = urllib.request.Request(url, headers=headers)
        try:
            return urllib.request.urlopen(req, timeout=30)
        except urllib.error.HTTPError as e:
            challenge = e.headers.get("WWW-Authenticate", "")
            if e.code != 401 or self.token or not challenge.startswith("Bearer "):
                raise
            params = dict(re.findall(r'(\w+)="([^"]*)"', challenge))
            realm = params.pop("realm")
            params.setdefault("scope", f"repository:{self.path}:pull")
            with urllib.request.urlopen(f"{realm}?{urllib.parse.urlencode(params)}", timeout=30) as r:
                body = json.load(r)
            self.token = body.get("token") or body.get("access_token")
            return self._get(url, accept)

    def tags(self) -> list[str]:
        out, url = [], f"https://{self.host}/v2/{self.path}/tags/list?n=1000"
        while url:
            with self._get(url) as r:
                out += json.load(r).get("tags") or []
                link = r.headers.get("Link", "")
            m = re.search(r"<([^>]+)>;\s*rel=\"next\"", link)
            url = urllib.parse.urljoin(f"https://{self.host}", m[1]) if m else None
        return out

    def created(self, tag: str) -> dt.datetime | None:
        """Creation time from the image config (linux/amd64)."""
        with self._get(f"https://{self.host}/v2/{self.path}/manifests/{tag}", ACCEPT) as r:
            manifest = json.load(r)
        if "manifests" in manifest:
            amd64 = [m for m in manifest["manifests"]
                     if m.get("platform", {}).get("architecture") == "amd64" and m.get("platform", {}).get("os") == "linux"]
            if not amd64:
                return None
            with self._get(f"https://{self.host}/v2/{self.path}/manifests/{amd64[0]['digest']}", ACCEPT) as r:
                manifest = json.load(r)
        digest = manifest.get("config", {}).get("digest")
        if not digest:
            return None
        with self._get(f"https://{self.host}/v2/{self.path}/blobs/{digest}") as r:
            created = json.load(r).get("created")
        m = re.match(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d", created or "")
        return dt.datetime.fromisoformat(m[0]).replace(tzinfo=dt.timezone.utc) if m else None


def version_key(tag: str) -> tuple[int, ...]:
    return tuple(int(p) for p in STABLE.match(tag)[2].split("."))


def newer(current: str, tags: list[str]) -> list[str]:
    prefix, nums = STABLE.match(current).groups()
    parts = nums.count(".") + 1
    same_shape = [t for t in tags if (m := STABLE.match(t)) and m[1] == prefix and m[2].count(".") + 1 == parts]
    return sorted((t for t in same_shape if version_key(t) > version_key(current)), key=version_key, reverse=True)


RELEASES = {
    "prom/prometheus": "https://github.com/prometheus/prometheus/releases",
    "grafana/grafana": "https://github.com/grafana/grafana/releases",
    "telegraf": "https://github.com/influxdata/telegraf/releases",
    "busybox": "https://busybox.net/",
}


def release_link(repo: str) -> str:
    if repo in RELEASES:
        return RELEASES[repo]
    host, path = split_repo(repo)
    if host == "ghcr.io":
        return f"https://github.com/{path}/releases"
    if path.startswith("library/"):
        return f"https://hub.docker.com/_/{path[8:]}"
    if path.startswith("tpill90/"):
        return f"https://github.com/{path}/releases"
    return f"https://hub.docker.com/r/{path}/tags"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--min-age-days", type=int, default=7)
    args = ap.parse_args()
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=args.min_age_days)

    texts = {f: open(f, encoding="utf-8").read() for f in FILES}
    pinned = sorted({(m["repo"], m["tag"]) for t in texts.values() for m in IMAGE_REF.finditer(t)})
    updates, notes = [], []
    for repo, tag in pinned:
        try:
            reg = Registry(*split_repo(repo))
            candidates = newer(tag, reg.tags())
            chosen = None
            for cand in candidates[:5]:          # newest first; skip ones that are too fresh
                created = reg.created(cand)
                if created and created <= cutoff:
                    chosen = cand
                    break
                notes.append(f"- `{repo}:{cand}` is newer but only from {created:%Y-%m-%d}; waiting." if created
                             else f"- `{repo}:{cand}`: no creation date, skipped.")
            if chosen:
                updates.append((repo, tag, chosen))
            print(f"{repo}:{tag} → {chosen or 'up to date'}", file=sys.stderr)
        except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
            notes.append(f"- `{repo}`: couldn't check ({exc})")

    if not updates:
        print("no updates")
        if notes:
            print("\n".join(notes))
        return

    lines = ["Newer stable versions of pinned images (at least "
             f"{args.min_age_days} days old):", "", "| Image | From | To | Notes |", "|---|---|---|---|"]
    for repo, old, new in updates:
        lines.append(f"| `{repo}` | `{old}` | `{new}` | [release notes]({release_link(repo)}) |")
        if args.apply:
            for f in FILES:
                texts[f] = texts[f].replace(f"{repo}:{old}", f"{repo}:{new}")
    if notes:
        lines += ["", *notes]
    lines += ["", "CI runs the full stack (DNS, web UI, metrics, Grafana) on this branch. "
              "Check the release notes for breaking changes before merging."]
    if args.apply:
        for f, t in texts.items():
            with open(f, "w", encoding="utf-8") as fh:
                fh.write(t)
    print("\n".join(lines))


if __name__ == "__main__":
    main()
