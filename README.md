# lanjaeger

A ready-to-run [lancache](https://lancache.net) stack for Docker Compose: one DNS server and one caching proxy, so every game download on your LAN happens once and then comes from local disk.

It runs the official `lancachenet/lancache-dns` and `lancachenet/monolithic` images, based on [lancachenet/docker-compose](https://github.com/lancachenet/docker-compose), with these changes:

- **Logs can't fill your disk.** A small `logrotate` service rotates `access.log` by size. Upstream relies on `LOGFILE_RETENTION`, which only deletes whole files by age and never touches the ever-growing `access.log` ([upstream #40](https://github.com/lancachenet/docker-compose/issues/40)).
- **`.env` isn't tracked.** You copy `.env.example` once, and `git pull` never conflicts with your settings.
- **No autostart script.** The restart policy is a setting (`RESTART_POLICY`), so nothing edits `docker-compose.yml` or needs `sudo`.
- **DNS health check.** `docker compose ps` shows whether cached domains actually resolve. Monolithic already has one.
- **Safer defaults and docs.** `CACHE_DISK_SIZE` is gigabytes only (`4t` breaks nginx), and compose stops with a clear message if `DNS_BIND_IP` or `CACHE_ROOT` is missing.
- **`update.sh`** pulls, recreates and removes old images, from any working directory.

## Quick start

```bash
git clone https://github.com/varmox/lanjaeger.git && cd lanjaeger
cp .env.example .env
nano .env            # set LANCACHE_IP, DNS_BIND_IP, CACHE_ROOT, CACHE_DISK_SIZE
docker compose up -d
docker compose ps    # dns and monolithic should turn "healthy" within ~2 minutes
```

Then make your clients use the cache's DNS: hand out `DNS_BIND_IP` as the **only** DNS server in your router's DHCP settings. Before a LAN party, go through **[NETWORK.md](NETWORK.md)**: IPv6 DNS, switches, uplink and the other network things that decide whether the cache actually gets used. **[SETUP.md](SETUP.md)** shows the recommended setup (diagram, IP plan, `.env`, router settings) and the test plan to run on it before the event. **[examples/](examples/README.md)** has example networks with router configurations, including a FortiGate with SD-WAN.

Once everything works, set `RESTART_POLICY=unless-stopped` in `.env` and run `docker compose up -d` again so the stack survives reboots.

## Settings

All settings live in `.env`. The comments in [`.env.example`](.env.example) explain each one. The ones you must change:

| Setting | What |
|---|---|
| `LANCACHE_IP` | IP(s) clients download from — this machine |
| `DNS_BIND_IP` | IP the DNS server listens on |
| `CACHE_ROOT` | Cache and log location — ideally a separate disk |
| `CACHE_DISK_SIZE` | Cache size limit, **gigabytes with `g` only** (`4000g`, not `4t`) |
| `CACHE_INDEX_SIZE` | About `250m` per `1000g` of cache |

## Ad blocking

Every device that uses this DNS gets ads, trackers and malware domains blocked, Pi-hole style, without installing anything on the device. This is done by [Blocky](https://github.com/0xERR0R/blocky), which sits in front of lancache-dns:

```
clients ──► blocky :53 ──► lancache-dns ──► UPSTREAM_DNS
```

- **Blocklist:** [Steven Black's unified hosts](https://github.com/StevenBlack/hosts) (ads and malware, conservative), refreshed daily, plus your own `blocky/denylist.txt`.
- **Never blocked:** anything in `blocky/allowlist.txt`. You can also allow a domain with one click in the web UI.
- **Keeps devices on local DNS:** Blocky answers the canary domains that Firefox's built-in DNS-over-HTTPS and iCloud Private Relay check, so those devices stay on this DNS. Without that, they'd bypass both the ad blocker and the game cache.
- **On/off and pauses:** in the web UI (Ad blocking page). Pausing for 5, 30 or 60 minutes resumes automatically. The off switch survives restarts. Without the web UI, blocking is always on.
- **API stays internal:** Blocky's API has no authentication, so it isn't published on the host. Only the web UI talks to it.

If something breaks (a shop's checkout, a game launcher's login), open **Ad blocking → Most blocked** in the web UI and click **Allow**, or add the domain to `blocky/allowlist.txt` and run `docker compose restart blocky`.

## DNS entries

Give devices on the LAN their own names, for example `nas.lan` or `cs2.lan` for a game server, under **DNS entries** in the web UI. You can also edit `blocky/hosts.txt` directly (format: `<IP> <name>  # note`).

- **No downtime:** Blocky re-reads the file every minute, so changes go live without restarting DNS. **Apply now** restarts Blocky for an instant change, at the cost of about 2 seconds without DNS and a reset of the 24-hour ad-blocking stats.
- **A and AAAA records, exact names only:** `nas.lan` doesn't cover `www.nas.lan`. Avoid `.local`, which belongs to mDNS/Bonjour.
- **Overrides the cache:** entries are answered before lancache-dns, so an entry for a game download domain overrides the cache for that name.
- **Test a name:** shows what the DNS server answers for any name, and whether the answer came from your entries, the ad blocker, the cache or upstream.

lancache-dns itself is [BIND 9](https://www.isc.org/bind/) with zones generated from [uklans/cache-domains](https://github.com/uklans/cache-domains). Blocky, in front of it, handles ad blocking, caching and these entries.

## Prefill

Fill the cache before people arrive, for example the night before a LAN party, so even the first install on every machine runs at LAN speed. Set it up in the web UI under **Prefill**. It uses [tpill90's prefill tools](https://github.com/tpill90) for Steam, Epic and Battle.net, started as one-off containers.

- **Steam:**
  - Choose your whole library, games played in the last two weeks, the most popular games on Steam, and/or specific games picked by searching the Steam store.
  - Steam only serves games to their owners, so you log in once. The UI shows the login prompts (username, password, Steam Guard code).
  - Steam returns a token that later runs use. Your password is never stored, and it's removed from the login transcript the UI keeps.
- **Epic Games:**
  - Your whole library, or games ticked from your own Epic library. The list comes from Epic using the tool's login.
  - Logging in once: open [legendary.gl/epiclogin](https://legendary.gl/epiclogin), sign in to Epic, and paste the `authorizationCode` it shows into the web UI.
  - Games that leave your library are dropped from the selection automatically. Otherwise the Epic tool would abort the whole run.
- **Battle.net:** tick the Blizzard, Activision and Microsoft games you want. No login needed.
- **Running it:** click **Prefill now**, or schedule it nightly at a time you choose. Services run one after another, never in parallel.
- **What gets downloaded:** only what changed since the last run. **Stop** is always safe; the next run continues where this one stopped.

Prefill containers use your `DNS_BIND_IP` as their DNS server, so downloads go through the cache exactly like a LAN client's. Their state (selections, login tokens, progress) lives in `prefill/`, which is git-ignored.

## Alerts

The web UI watches the stack and messages you on [ntfy](https://ntfy.sh) (phone push), Discord and/or email when:

- the cache disk is fuller than a percentage, or has less than a minimum of free space (default 90% / 50 GB);
- a service (DNS, ad blocker, cache, log rotation) is stopped or unhealthy for more than a few minutes (default 5);
- a prefill run fails (the message includes the end of its log);
- the ad blocklist is empty, which means its download failed.

Each problem is announced once, repeated every few hours while it lasts (default 12), and followed by a "resolved" message. Short blips within the grace period don't alert. Stopping a prefill yourself doesn't count as a failure.

Set it up under **Alerts** in the web UI. "Send test alert" shows per channel whether delivery worked. Webhook URLs and the SMTP password are stored in `alerts/` (git-ignored) and never sent back to the browser. Active alerts also show as a banner on the dashboard.

## Web UI (optional)

A dashboard and control panel, built with Angular and VMware's [Clarity](https://github.com/vmware-clarity/ng-clarity) design system.

- **Dashboard:** cache hit rate, internet traffic saved, a 24-hour traffic chart, per-service stats (Steam, Epic, Riot, Blizzard…), active downloads, cache disk usage and container health.
- **Clients:** traffic per device on your LAN.
- **Services:** restart DNS, cache or log rotation, read their logs, and pull and apply new images.
- **Settings:** edit `.env` values with validation (it refuses `4t`, malformed IPs and so on) and apply them.

Enable it in `.env`:

```bash
COMPOSE_PROFILES=webui
WEBUI_PASSWORD=<a long random password>
```

Then run `docker compose up -d --build` and open `http://<server>:8080`. The first build takes a few minutes because it compiles the frontend.

> **Security:** the web UI mounts the Docker socket so it can restart and update containers. That is root-equivalent access to the host. Keep it on your LAN (set `WEBUI_BIND_IP` to your LAN IP), use a strong password, and never forward the port to the internet. Without `WEBUI_PASSWORD` it runs read-only. `WEBUI_PUBLIC_DASHBOARD=true` lets anyone on the LAN view stats without signing in, for example on a screen at a LAN party; controls still need the password.

Stats come from the cache's `access.log`. After a restart the UI replays the last 64 MB of the log, so recent history survives restarts.

## Updating

```bash
./update.sh
```

## Checking it works

With the web UI, open `http://<server>:8080/check` on any device. It says in plain words whether that device's downloads go through the cache, and what to fix if not: a second DNS server, IPv6 DNS from the router, the browser's own encrypted DNS. No sign-in needed, so players can run it themselves.

By hand:

```bash
# From a client: should return LANCACHE_IP
nslookup lancache.steamcontent.com <DNS_BIND_IP>

# On the server, while a client downloads: HIT means served from cache
tail -f "$CACHE_ROOT/logs/access.log"
```

## Troubleshooting

- **Port 53 already in use.** Another DNS server is bound to the same IP (dnsmasq, Pi-hole, a second resolver). Ubuntu's `systemd-resolved` only listens on `127.0.0.53` and doesn't conflict as long as `DNS_BIND_IP` is your LAN IP.
- **Downloads bypass the cache.** Check the client really uses `DNS_BIND_IP` (`nslookup` above). Browsers and some antivirus suites use DNS-over-HTTPS or "DNS protection" that bypasses local DNS; turn that off on clients.
- **nginx won't start after changing the cache size.** Use `g`, not `t`, in `CACHE_DISK_SIZE`.
- **More:** [lancache.net FAQ](https://lancache.net/docs/faq/).

## Credits

Built on the work of the [LanCache.NET](https://lancache.net) team and the [uklans/cache-domains](https://github.com/uklans/cache-domains) list. This repository only contains configuration; the images are theirs.
