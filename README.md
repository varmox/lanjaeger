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

Then make your clients use the cache's DNS: hand out `DNS_BIND_IP` as the DNS server in your router's DHCP settings. Alternatively, load the [uklans/cache-domains](https://github.com/uklans/cache-domains) lists into your existing DNS (Pi-hole, Unbound, …).

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

## Updating

```bash
./update.sh
```

## Checking it works

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
