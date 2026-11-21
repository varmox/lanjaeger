"""Cache prefill: download games into the lancache before anyone needs them.

Drives tpill90's prefill tools (steam-, epic- and battlenet-lancache-prefill)
as one-off Docker containers:

- Containers use the default bridge network with DNS = DNS_BIND_IP, so game
  CDN names resolve to the cache exactly like for a LAN client.
- Their config (login token, selection, "already prefilled" state) lives in
  <project>/prefill/<service>/ and their manifest cache in .../cache, so runs
  only download what changed.
- Steam and Epic need a one-time login, run in a TTY container whose prompts
  (Steam: username, password, Steam Guard code; Epic: an authorization code
  from the browser) are relayed through the web UI. The tools store tokens;
  passwords are never written anywhere and are scrubbed from our transcript.
- Epic has no store search we could map to its app ids, so the UI offers the
  user's own library, read with the tool's access token while it is valid
  (never refreshed by us — Epic rotates refresh tokens, so that would log
  the tool out). An expired token is refreshed by a no-op tool run.
- Runs are sequential (one service at a time) so they don't fight over
  bandwidth, and optionally scheduled nightly.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import platform
import re
import socket
import threading
import time
import urllib.parse
import urllib.request

from . import docker_api, envfile

STEAM_IMAGE = "tpill90/steam-lancache-prefill:v3.7.2"
EPIC_IMAGE = "tpill90/epic-lancache-prefill:v2.1.0"
BATTLENET_IMAGE = "tpill90/battlenet-lancache-prefill:v2.3.0"

# Per tool: image, where it expects its config dir, and its login token file.
SERVICES = {
    "steam": {"image": STEAM_IMAGE, "config": "/Config", "token": "account.config"},
    "epic": {"image": EPIC_IMAGE, "config": "/Config", "token": "userAccount.json"},
    "battlenet": {"image": BATTLENET_IMAGE, "config": "/app/Config", "token": None},
}
SERVICE_ORDER = ["steam", "epic", "battlenet"]
LABELS = {"steam": "Steam", "epic": "Epic Games", "battlenet": "Battle.net"}
OS_CHOICES = ("windows", "linux", "macos")

# From BattleNetPrefill/Structs/TactProduct.cs (v2.3.0).
BATTLENET_PRODUCTS = [
    {"code": "rtro", "name": "Blizzard Arcade Collection", "publisher": "Blizzard"},
    {"code": "drtl", "name": "Diablo", "publisher": "Blizzard"},
    {"code": "anbs", "name": "Diablo: Immortal", "publisher": "Blizzard"},
    {"code": "osi", "name": "Diablo 2: Resurrected", "publisher": "Blizzard"},
    {"code": "d3", "name": "Diablo 3", "publisher": "Blizzard"},
    {"code": "fenris", "name": "Diablo 4", "publisher": "Blizzard"},
    {"code": "hsb", "name": "Hearthstone", "publisher": "Blizzard"},
    {"code": "hero", "name": "Heroes of the Storm", "publisher": "Blizzard"},
    {"code": "s1", "name": "Starcraft Remastered", "publisher": "Blizzard"},
    {"code": "s2", "name": "Starcraft 2", "publisher": "Blizzard"},
    {"code": "pro", "name": "Overwatch 2", "publisher": "Blizzard"},
    {"code": "w1r", "name": "Warcraft 1: Remastered", "publisher": "Blizzard"},
    {"code": "w2bn", "name": "WarCraft II: Battle.net Edition", "publisher": "Blizzard"},
    {"code": "w2r", "name": "WarCraft II: Remastered", "publisher": "Blizzard"},
    {"code": "w3", "name": "Warcraft 3: Reforged", "publisher": "Blizzard"},
    {"code": "gryphon", "name": "Warcraft Rumble", "publisher": "Blizzard"},
    {"code": "wow", "name": "World of Warcraft", "publisher": "Blizzard"},
    {"code": "wow_classic", "name": "WoW Cataclysm Classic", "publisher": "Blizzard"},
    {"code": "wow_classic_era", "name": "WoW Classic", "publisher": "Blizzard"},
    {"code": "viper", "name": "Call of Duty: Black Ops 4", "publisher": "Activision"},
    {"code": "btlr", "name": "Call of Duty: Black Ops 6", "publisher": "Activision"},
    {"code": "zeus", "name": "Call of Duty: Black Ops Cold War", "publisher": "Activision"},
    {"code": "odin", "name": "Call of Duty: Modern Warfare 2019", "publisher": "Activision"},
    {"code": "nina", "name": "Call of Duty: Modern Warfare II", "publisher": "Activision"},
    {"code": "pinta", "name": "Call of Duty: Modern Warfare III", "publisher": "Activision"},
    {"code": "auks", "name": "Call of Duty", "publisher": "Activision"},
    {"code": "lazr", "name": "Call of Duty: MW2 Remastered", "publisher": "Activision"},
    {"code": "fore", "name": "Call of Duty: Vanguard", "publisher": "Activision"},
    {"code": "wlby", "name": "Crash Bandicoot 4: It's About Time", "publisher": "Activision"},
    {"code": "aqua", "name": "Avowed", "publisher": "Microsoft"},
    {"code": "scor", "name": "Sea of Thieves", "publisher": "Microsoft"},
]
BATTLENET_CODES = {p["code"] for p in BATTLENET_PRODUCTS}

DEFAULT_SETTINGS = {
    "steam": {"enabled": False, "all": False, "recent": False, "top": 0, "os": ["windows"], "games": {}},
    "epic": {"enabled": False, "all": False, "games": {}},
    "battlenet": {"enabled": False, "products": []},
    "schedule": {"enabled": False, "time": "03:00"},
}

ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07|\x1b[()][A-Z0-9]|\r(?!\n)")


def strip_ansi(s: str) -> str:
    return ANSI.sub("", s)


def _read_text(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return None


# ── settings ────────────────────────────────────────────────────────────


class Store:
    def __init__(self, project_dir: str):
        self.root = os.path.join(project_dir, "prefill")
        self.path = os.path.join(self.root, "settings.json")
        self._lock = threading.Lock()

    def dir(self, service: str, sub: str = "config") -> str:
        d = os.path.join(self.root, service, sub)
        os.makedirs(d, exist_ok=True)
        return d

    def load(self) -> dict:
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            data = {}
        merged = json.loads(json.dumps(DEFAULT_SETTINGS))
        for k, v in data.items():
            if k in merged and isinstance(v, dict):
                merged[k].update(v)
        return merged

    def save(self, settings: dict) -> dict:
        clean = validate(settings)
        with self._lock:
            os.makedirs(self.root, exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(clean, f, indent=1)
            os.replace(tmp, self.path)
            # The tools read their own selection files.
            self.write_selection("steam", [int(i) for i in clean["steam"]["games"]])
            self.write_selection("epic", list(clean["epic"]["games"]))
        return clean

    def selection_path(self, service: str) -> str:
        return os.path.join(self.dir(service), "selectedAppsToPrefill.json")

    def write_selection(self, service: str, ids: list) -> None:
        with open(self.selection_path(service), "w", encoding="utf-8") as f:
            json.dump(ids, f)

    def logged_in(self, service: str) -> bool:
        token = SERVICES[service]["token"]
        return token is None or os.path.exists(os.path.join(self.dir(service), token))

    def logout(self, service: str) -> None:
        token = SERVICES[service]["token"]
        if token:
            try:
                os.remove(os.path.join(self.dir(service), token))
            except FileNotFoundError:
                pass




def validate(s: dict) -> dict:
    if not isinstance(s, dict):
        raise ValueError("settings must be an object")
    st, bn, sc = s.get("steam") or {}, s.get("battlenet") or {}, s.get("schedule") or {}
    ep = s.get("epic") or {}
    epic_games = ep.get("games") or {}
    if not isinstance(epic_games, dict) or len(epic_games) > 5000:
        raise ValueError("epic.games must be an object of {appId: title}")
    for k in epic_games:
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,100}", str(k)):
            raise ValueError(f"invalid Epic app id {k!r}")
    games = st.get("games") or {}
    if not isinstance(games, dict) or len(games) > 2000:
        raise ValueError("steam.games must be an object of at most 2000 {appId: name}")
    clean_games = {}
    for k, v in games.items():
        if not re.fullmatch(r"\d{1,10}", str(k)):
            raise ValueError(f"invalid Steam app id {k!r}")
        clean_games[str(int(k))] = str(v)[:200]
    os_list = [o for o in (st.get("os") or ["windows"]) if o in OS_CHOICES] or ["windows"]
    top = st.get("top", 0)
    if not isinstance(top, int) or not 0 <= top <= 100:
        raise ValueError("steam.top must be 0–100")
    products = bn.get("products") or []
    if not isinstance(products, list) or any(p not in BATTLENET_CODES for p in products):
        raise ValueError("unknown Battle.net product")
    when = str(sc.get("time", "03:00"))
    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", when):
        raise ValueError("schedule.time must be HH:MM")
    return {
        "steam": {"enabled": bool(st.get("enabled")), "all": bool(st.get("all")), "recent": bool(st.get("recent")),
                  "top": top, "os": sorted(set(os_list)), "games": clean_games},
        "epic": {"enabled": bool(ep.get("enabled")), "all": bool(ep.get("all")),
                 "games": {str(k): str(v)[:200] for k, v in epic_games.items()}},
        "battlenet": {"enabled": bool(bn.get("enabled")), "products": sorted(set(products))},
        "schedule": {"enabled": bool(sc.get("enabled")), "time": when},
    }


# ── Steam store search (for picking games) ──────────────────────────────


def steam_search(term: str) -> list[dict]:
    term = term.strip()[:100]
    if len(term) < 2:
        return []
    url = "https://store.steampowered.com/api/storesearch/?" + urllib.parse.urlencode(
        {"term": term, "l": "english", "cc": "US"})
    req = urllib.request.Request(url, headers={"User-Agent": "lanjaeger"})
    with urllib.request.urlopen(req, timeout=8) as r:
        items = json.load(r).get("items", [])
    return [{"id": str(i["id"]), "name": i["name"],
             "image": f"https://cdn.akamai.steamstatic.com/steam/apps/{i['id']}/capsule_sm_120.jpg"}
            for i in items if i.get("type") == "app"][:20]


# ── container plumbing ──────────────────────────────────────────────────


def _image(base: str) -> str:
    # The Steam tool publishes separate -arm64 tags.
    if "steam" in base and platform.machine() in ("aarch64", "arm64"):
        return base + "-arm64"
    return base


def _config_target(service: str) -> str:
    # Steam's image symlinks /app/Config → /Config, Epic's binary lives in /,
    # Battle.net's in /app without a symlink.
    return SERVICES[service]["config"]


def container_name(project: str, service: str) -> str:
    return f"{project}-prefill-{service}"


def _container_config(store: Store, service: str, cmd: list[str], dns_ip: str | None, tty: bool) -> dict:
    image = _image(SERVICES[service]["image"])
    if not docker_api.image_exists(image):
        docker_api.pull(image)
    return {
        "Image": image,
        "Cmd": cmd,
        "Tty": tty,
        "OpenStdin": tty,
        "StdinOnce": False,
        "AttachStdin": tty,
        "AttachStdout": tty,
        "AttachStderr": tty,
        "Env": ["XDG_CACHE_HOME=/Cache", "TERM=xterm"],
        "Labels": {"lanjaeger.prefill": service},
        "HostConfig": {
            "Binds": [f"{store.dir(service)}:{_config_target(service)}", f"{store.dir(service, 'cache')}:/Cache"],
            "Dns": [dns_ip] if dns_ip else [],
            "NetworkMode": "bridge",
        },
    }


def steam_args(st: dict) -> list[str]:
    args = ["prefill", "--no-ansi"]
    if st["all"]:
        args.append("--all")
    if st["recent"]:
        args.append("--recent")
    if st["top"]:
        args += ["--top", str(st["top"])]
    args += ["--os", *st["os"]]
    return args


def battlenet_args(bn: dict) -> list[str]:
    return ["prefill", "--no-ansi", "--products", *bn["products"]]


def epic_args(ep: dict) -> list[str]:
    return ["prefill", "--no-ansi", *(["--all"] if ep["all"] else [])]


def args_for(service: str, settings: dict) -> list[str]:
    return {"steam": steam_args, "epic": epic_args, "battlenet": battlenet_args}[service](settings[service])


# ── runner ──────────────────────────────────────────────────────────────


class Runner:
    """Runs prefills one service at a time; scheduler included."""

    def __init__(self, project_dir: str, project: str | None, env_path: str):
        self.store = Store(project_dir)
        self.project = project or "lanjaeger"
        self.env_path = env_path
        self._lock = threading.Lock()
        self.queue: list[str] = []
        self.current: str | None = None
        self.cancelled = False
        self.last_scheduled: str | None = None
        self.epic_refresh: dict = {"state": "idle", "message": ""}

    def dns_ip(self) -> str | None:
        return envfile.read(self.env_path).get("DNS_BIND_IP") or None

    def runnable(self, settings: dict) -> list[str]:
        out = []
        st, ep, bn = settings["steam"], settings["epic"], settings["battlenet"]
        if st["enabled"] and (st["games"] or st["all"] or st["recent"] or st["top"]) and self.store.logged_in("steam"):
            out.append("steam")
        if ep["enabled"] and (ep["games"] or ep["all"]) and self.store.logged_in("epic"):
            out.append("epic")
        if bn["enabled"] and bn["products"]:
            out.append("battlenet")
        return out

    def busy(self) -> bool:
        return bool(self.current or self.queue)

    def start(self, services: list[str]) -> list[str]:
        settings = self.store.load()
        allowed = self.runnable(settings)
        wanted = [s for s in SERVICE_ORDER if s in services and s in allowed]
        if not wanted:
            raise ValueError("nothing to run — enable a service, select games, and log in where needed")
        with self._lock:
            if self.busy():
                raise RuntimeError("a prefill is already running")
            self.queue = list(wanted)
            self.cancelled = False
        threading.Thread(target=self._work, daemon=True, name="prefill").start()
        return wanted

    def _work(self) -> None:
        while True:
            with self._lock:
                if self.cancelled or not self.queue:
                    self.queue, self.current = [], None
                    return
                self.current = self.queue.pop(0)
            service = self.current
            try:
                settings = self.store.load()
                if service == "epic":
                    self._prune_epic_selection(settings)
                if self.run_container(service, args_for(service, settings)) == 0 and service == "steam":
                    record_steam_success(self.store, settings["steam"]["games"])
            except docker_api.DockerError as exc:
                self._note_error(service, str(exc))

    def run_container(self, service: str, cmd: list[str], suffix: str = "") -> int | None:
        """Run a tool container to completion; returns its exit code."""
        name = container_name(self.project, service + suffix)
        docker_api.remove(name)
        cid = docker_api.create(name, _container_config(self.store, service, cmd, self.dns_ip(), tty=False))
        docker_api.start(cid)
        while (info := docker_api.inspect(name)) and info["State"]["Running"]:
            time.sleep(5)
        return info["State"]["ExitCode"] if info else None

    def _prune_epic_selection(self, settings: dict) -> None:
        """The Epic tool crashes the whole run on a selected app that isn't in
        the library any more, so drop those (when we know the library)."""
        lib = read_epic_library(self.store)
        if not lib.get("games"):
            return
        owned = {g["id"] for g in lib["games"]}
        keep = [i for i in settings["epic"]["games"] if i in owned]
        self.store.write_selection("epic", keep)

    def refresh_epic_library(self) -> None:
        """Get a fresh token via a no-op tool run if needed, then read the
        library. Runs in the background; progress is in self.epic_refresh."""
        with self._lock:
            if self.busy():
                raise RuntimeError("a prefill is already running")
            self.current = "epic-refresh"
        self.epic_refresh = {"state": "running", "message": ""}
        threading.Thread(target=self._refresh_epic, daemon=True).start()

    def _refresh_epic(self) -> None:
        sel = self.store.selection_path("epic")
        saved = _read_text(sel) or "[]"
        try:
            try:
                fetch_epic_library(self.store)
            except TokenExpired:
                self.store.write_selection("epic", [])   # no-op run: log in, download nothing
                try:
                    self.run_container("epic", ["prefill", "--no-ansi"], suffix="-refresh")
                finally:
                    with open(sel, "w", encoding="utf-8") as f:
                        f.write(saved)
                fetch_epic_library(self.store)
            self.epic_refresh = {"state": "done", "message": ""}
        except (TokenExpired, docker_api.DockerError, OSError, ValueError) as exc:
            self.epic_refresh = {"state": "failed", "message": str(exc)}
        finally:
            with self._lock:
                self.current = None

    def _note_error(self, service: str, msg: str) -> None:
        path = os.path.join(self.store.root, f"{service}-error.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"{dt.datetime.now().isoformat(timespec='seconds')} {msg}\n")

    def stop(self) -> None:
        with self._lock:
            self.cancelled = True
            self.queue = []
            current = self.current
        if current:
            docker_api.stop(container_name(self.project, current))

    def status(self) -> dict:
        out = {"running": self.current if self.current in SERVICES else None,
               "refreshingEpic": self.current == "epic-refresh",
               "queued": list(self.queue), "services": {}}
        for service in SERVICE_ORDER:
            name = container_name(self.project, service)
            info = None
            try:
                info = docker_api.inspect(name)
            except docker_api.DockerError:
                pass
            state = info["State"] if info else None
            err_path = os.path.join(self.store.root, f"{service}-error.txt")
            out["services"][service] = {
                "lastRun": None if not state else {
                    "running": state["Running"],
                    "exitCode": state["ExitCode"],
                    "startedAt": state["StartedAt"],
                    "finishedAt": None if state["Running"] else state["FinishedAt"],
                },
                "log": strip_ansi(docker_api.container_logs(name, 200)) if info else "",
                "error": _read_text(err_path) if not state else None,
            }
        return out

    def last_runs(self) -> dict:
        """Per service: {running, exitCode, finished (unix time or None)} of
        the last tool container, without its log (cheap enough for scrapes)."""
        out = {}
        for service in SERVICE_ORDER:
            info = docker_api.inspect(container_name(self.project, service))
            if info:
                st = info["State"]
                out[service] = {"running": st["Running"], "exitCode": st["ExitCode"],
                                "finished": None if st["Running"] else _docker_time(st.get("FinishedAt", ""))}
        return out

    # Nightly schedule
    def tick(self, now: dt.datetime | None = None) -> bool:
        now = dt.datetime.now() if now is None else now
        settings = self.store.load()
        sc = settings["schedule"]
        today = now.date().isoformat()
        if not sc["enabled"] or self.last_scheduled == today or now.strftime("%H:%M") < sc["time"]:
            return False
        self.last_scheduled = today
        try:
            self.start(self.runnable(settings))
            return True
        except (ValueError, RuntimeError):
            return False

    def run_scheduler(self) -> None:
        # Don't fire for a time that already passed before we started.
        now = dt.datetime.now()
        if now.strftime("%H:%M") >= self.store.load()["schedule"]["time"]:
            self.last_scheduled = now.date().isoformat()
        while True:
            try:
                self.tick()
            except Exception:  # never let the scheduler thread die
                pass
            time.sleep(30)


# ── Epic library (for picking games) ────────────────────────────────────

EPIC_LAUNCHER = "https://launcher-public-service-prod06.ol.epicgames.com"
EPIC_CATALOG = "https://catalog-public-service-prod06.ol.epicgames.com"
EPIC_EXCLUDED_NAMESPACES = {"ue", "89efe5924d3d467c839449ab6ab52e7f"}  # Unreal Engine assets (as the tool does)


class TokenExpired(Exception):
    pass


def _docker_time(s: str) -> int | None:
    """Docker's RFC 3339 time (nanoseconds, 'Z') → unix seconds; None for 'never'."""
    try:
        t = dt.datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return None
    return int(t.timestamp()) if t.year >= 1970 else None


# ── what has been prefilled ─────────────────────────────────────────────
#
# Epic and Battle.net tools record finished games themselves; Steam records
# depots, which can't be mapped to games offline, so for Steam we note the
# explicitly selected games after a run that exited with 0.


def _steam_record_path(store: Store) -> str:
    return os.path.join(store.root, "steam", "prefilled.json")


def record_steam_success(store: Store, games: dict, now: float | None = None) -> None:
    path = _steam_record_path(store)
    try:
        with open(path, encoding="utf-8") as f:
            done = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        done = {}
    ts = int(time.time() if now is None else now)
    for app_id, name in games.items():
        done[str(app_id)] = {"name": name, "time": ts}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(done, f)
    os.replace(path + ".tmp", path)


def _load_json(path: str):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def steam_depot_count(store: Store) -> int | None:
    depots = _load_json(os.path.join(store.root, "steam", "config", "successfullyDownloadedDepots.json"))
    return len(depots) if isinstance(depots, dict) else None


def prefilled(store: Store) -> list[dict]:
    """Games prefilled at least once: [{service, id, name, time}]."""
    out = []
    steam = _load_json(_steam_record_path(store))
    if isinstance(steam, dict):
        out += [{"service": "steam", "id": k, "name": v.get("name") or k, "time": int(v.get("time") or 0)}
                for k, v in steam.items() if isinstance(v, dict)]

    epic_path = os.path.join(store.root, "epic", "config", "successfullyDownloadedApps.json")
    epic = _load_json(epic_path)
    if isinstance(epic, dict):
        titles = {g["id"]: g["title"] for g in read_epic_library(store).get("games", [])}
        titles.update(store.load()["epic"]["games"])
        mtime = int(os.path.getmtime(epic_path))
        out += [{"service": "epic", "id": k, "name": titles.get(k, k), "time": mtime} for k in epic]

    bn_dir = os.path.join(store.root, "battlenet", "cache", "BattlenetPrefill", "v1")
    names = {p["code"]: p["name"] for p in BATTLENET_PRODUCTS}
    try:
        files = sorted(os.listdir(bn_dir))
    except OSError:
        files = []
    for f in files:
        m = re.fullmatch(r"prefilledVersion-(.+)\.txt", f)
        if m:
            out.append({"service": "battlenet", "id": m[1], "name": names.get(m[1], m[1]),
                        "time": int(os.path.getmtime(os.path.join(bn_dir, f)))})
    return out


def _parse_dotnet_time(s: str) -> dt.datetime:
    """.NET writes 7 fractional digits; Python takes at most 6."""
    s = re.sub(r"(\.\d{6})\d+", r"\1", s.strip()).replace("Z", "+00:00")
    t = dt.datetime.fromisoformat(s)
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def epic_access_token(store: Store) -> str:
    """The tool's access token if it is still valid for a while. We only ever
    *use* it — never refresh — so the tool's own refresh token stays valid."""
    path = os.path.join(store.dir("epic"), SERVICES["epic"]["token"])
    try:
        with open(path, encoding="utf-8") as f:
            tok = json.load(f)
        expires = _parse_dotnet_time(tok["expires_at"])
    except (FileNotFoundError, KeyError, ValueError, json.JSONDecodeError):
        raise TokenExpired("not logged in") from None
    if expires - dt.datetime.now(dt.timezone.utc) < dt.timedelta(minutes=2):
        raise TokenExpired("access token expired")
    return tok["access_token"]


def _epic_get(url: str, token: str):
    req = urllib.request.Request(url, headers={"Authorization": f"bearer {token}", "User-Agent": "lanjaeger"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def library_path(store: Store) -> str:
    return os.path.join(store.root, "epic", "library.json")


def read_epic_library(store: Store) -> dict:
    try:
        with open(library_path(store), encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"updated": None, "games": []}


def fetch_epic_library(store: Store) -> dict:
    """Owned Epic games as [{id, title}], cached in prefill/epic/library.json."""
    token = epic_access_token(store)
    assets = _epic_get(f"{EPIC_LAUNCHER}/launcher/api/public/assets/Windows?label=Live", token)
    assets = [a for a in assets if a.get("namespace") not in EPIC_EXCLUDED_NAMESPACES and a.get("appName")]
    known = {g["id"]: g["title"] for g in read_epic_library(store).get("games", [])}

    def title(a: dict) -> tuple[str, str]:
        if a["appName"] in known:
            return a["appName"], known[a["appName"]]
        q = urllib.parse.urlencode({"id": a["catalogItemId"], "includeDLCDetails": "true",
                                    "includeMainGameDetails": "true", "country": "US", "locale": "en"})
        try:
            data = _epic_get(f"{EPIC_CATALOG}/catalog/api/shared/namespace/{a['namespace']}/bulk/items?{q}", token)
            return a["appName"], next(iter(data.values()))["title"]
        except (OSError, StopIteration, KeyError, ValueError):
            return a["appName"], a["appName"]

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=8) as pool:
        games = sorted(({"id": i, "title": t} for i, t in pool.map(title, assets)),
                       key=lambda g: g["title"].lower())
    lib = {"updated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "games": games}
    os.makedirs(os.path.dirname(library_path(store)), exist_ok=True)
    with open(library_path(store), "w", encoding="utf-8") as f:
        json.dump(lib, f)
    return lib


# ── login relay (Steam, Epic) ───────────────────────────────────────────


class Login:
    """One interactive login per service, relayed through the web UI.

    Steam: `select-apps status` logs in and exits.
    Epic: a prefill run with an *empty* selection logs in, lists the library
    and exits without downloading (the real selection is restored after).
    """

    TIMEOUT_S = 600
    COMMANDS = {
        "steam": ["select-apps", "status", "--no-ansi"],
        "epic": ["prefill", "--no-ansi"],
    }

    def __init__(self, runner: Runner, service: str):
        assert service in self.COMMANDS
        self.runner, self.service = runner, service
        self._lock = threading.Lock()
        self.sock: socket.socket | None = None
        self.output = ""
        self.state = "idle"        # idle | running | finished
        self.exit_code: int | None = None
        self.started = 0.0
        self._secrets: list[str] = []
        self._saved_selection: str | None = None

    def _scrub(self, text: str) -> str:
        for s in self._secrets:
            text = text.replace(s, "•" * 8)
        return text

    def _swap_out_selection(self) -> None:
        path = self.runner.store.selection_path(self.service)
        self._saved_selection = _read_text(path)
        self.runner.store.write_selection(self.service, [])

    def _restore_selection(self) -> None:
        if self.service != "epic":
            return
        path = self.runner.store.selection_path(self.service)
        with open(path, "w", encoding="utf-8") as f:
            f.write(self._saved_selection if self._saved_selection is not None else "[]")

    def start(self) -> None:
        with self._lock:
            if self.state == "running":
                raise RuntimeError("a login is already in progress")
            if self.runner.busy():
                raise RuntimeError("wait until the running prefill has finished")
            self.output, self.exit_code, self._secrets = "", None, []
            self.state, self.started = "running", time.time()
        name = container_name(self.runner.project, f"{self.service}-login")
        try:
            if self.service == "epic":
                self._swap_out_selection()
            docker_api.remove(name)
            cfg = _container_config(self.runner.store, self.service, self.COMMANDS[self.service],
                                    self.runner.dns_ip(), tty=True)
            cid = docker_api.create(name, cfg)
            self.sock, early = docker_api.attach(cid)
            docker_api.start(cid)
        except docker_api.DockerError:
            self._restore_selection()
            self.state = "finished"
            raise
        self._append(early)
        threading.Thread(target=self._read, args=(name,), daemon=True).start()

    def _append(self, raw: bytes) -> None:
        text = self._scrub(strip_ansi(raw.decode("utf-8", errors="replace")))
        with self._lock:
            self.output = (self.output + text)[-20_000:]

    def _read(self, name: str) -> None:
        sock = self.sock
        sock.settimeout(1.0)
        while True:
            if time.time() - self.started > self.TIMEOUT_S:
                docker_api.stop(name)
            try:
                chunk = sock.recv(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            if not chunk:
                break
            self._append(chunk)
        try:
            info = docker_api.inspect(name)
            self.exit_code = info["State"]["ExitCode"] if info else None
            docker_api.remove(name)
        except docker_api.DockerError:
            pass
        self._restore_selection()
        if self.service == "epic" and self.runner.store.logged_in("epic"):
            try:
                fetch_epic_library(self.runner.store)
            except (TokenExpired, OSError, ValueError):
                pass
        self.state = "finished"
        sock.close()

    def send(self, text: str, secret: bool) -> None:
        if self.state != "running" or not self.sock:
            raise RuntimeError("no login in progress")
        if len(text) > 200 or "\n" in text or "\r" in text:
            raise ValueError("invalid input")
        if secret and text:
            self._secrets.append(text)
        self.sock.sendall(text.encode() + b"\r")

    def status(self) -> dict:
        lines = [l for l in self.output.splitlines() if l.strip()]
        last = lines[-1] if lines else ""
        return {
            "state": self.state,
            "exitCode": self.exit_code,
            "output": self.output,
            "prompt": last if self.state == "running" else "",
            "secret": bool(re.search(r"password|authorizationCode", last, re.I)),
            "loggedIn": self.runner.store.logged_in(self.service),
        }

