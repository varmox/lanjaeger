# Network checklist for a LAN party

lanjaeger caches downloads and filters ads only for devices that actually use its DNS and can reach it quickly. Most "the cache doesn't work" problems are network problems. Go through this before people arrive. For complete example networks with router configurations (including a FortiGate with SD-WAN), see [examples/](examples/README.md).

## Must-haves

**1. Hand out lanjaeger as the only DNS server.**
In your router's DHCP settings, set the DNS server to `DNS_BIND_IP` and nothing else, with no secondary. A device with two DNS servers uses both and skips the cache for about half its downloads ([lancache: common issues](https://lancache.net/docs/common-issues/)).

**2. Don't let IPv6 hand out a different DNS server.**
Many routers announce their own DNS over IPv6 (router advertisements, RDNSS, DHCPv6). IPv6-capable devices then use it silently and skip both the cache and the ad blocker. Pick one fix:
- **Simplest:** turn IPv6 off on the party network.
- **Keep IPv6:** let lanjaeger serve DNS over IPv6 as well (see [IPv6 DNS](#ipv6-dns) below) and set the router's announced IPv6 DNS to `DNS_BIND_IP6`.

Some ISP routers keep announcing DNS even with that option off, so test with the [client check](#test-every-device).

**3. Give the cache server a private address.**
`LANCACHE_IP` must be in 192.168.x, 10.x or 172.16–31.x. Origin/EA and Riot only fall back to cacheable downloads when the cache resolves to a private IP. The web UI's Settings page warns if it doesn't.

**4. Keep `UPSTREAM_DNS` pointing outward.**
Use your router or a public resolver, never `DNS_BIND_IP` itself, which would create a loop. The Settings page warns about this too.

## Strongly recommended

**5. Catch hard-coded DNS on the router.**
Some devices and games query 8.8.8.8 or 1.1.1.1 directly. If you control the router, either redirect all outgoing DNS (UDP/TCP 53) from the party network to `DNS_BIND_IP`, or block it, along with DNS-over-TLS on port 853. Exempt lanjaeger itself, since it needs to reach `UPSTREAM_DNS`.

**6. Give the cache server the fastest link.**
Every client downloads from the cache at once, so its link is the bottleneck: 1 Gbit/s shared by 20 players is about 50 Mbit/s each. Beyond about 10 players, use a 10 GbE link (or bonded 1 GbE ports) from the cache server to the core switch, and 1 GbE to each table. Use SSD or NVMe storage for more than home use ([lancache: hardware](https://lancache.net/docs/hardware/)).

**7. Use a managed core switch, and guard against loops and rogue DHCP.**
- **Rogue DHCP:** guests plug in routers that start handing out their own addresses. Turn on DHCP snooping, with only the uplink to your real DHCP server trusted.
- **Loops:** turn on loop protection on table switches. Guests bridging Wi-Fi and Ethernet are the classic cause.
- **Storm control:** older LAN-party guides recommend turning it off, because game-discovery broadcasts can trip it ([SmallNetBuilder](https://www.smallnetbuilder.com/lanwan/lanwan-howto/lanpartyhowtopt3/)). Advice differs here; test with your switches.

**8. One flat, wired party network.**
LAN game discovery relies on broadcasts, so keep all players in one subnet: no client isolation, and no VLAN per table. Keep the party separate from your home network. Make the DHCP pool large enough for phones too (a /23 gives about 500 addresses). Put Wi-Fi on a separate guest network.

**9. Keep online games playable during downloads.**
Downloads that miss the cache can fill your internet line, and everyone's ping goes up. Enable SQM with CAKE (or fq_codel on weak routers) on the router ([OpenWrt SQM](https://openwrt.org/docs/guide-user/network/traffic-shaping/sqm)). On a FortiGate, use traffic shaping policies instead ([examples/fortigate.md](examples/fortigate.md#5-traffic-shaping-policy--objects--traffic-shaping)). Run [prefill](README.md#prefill) the night before, so the big downloads happen before people arrive.

## Test every device

Open the client check on each machine (or ask players to):

```
http://<DNS_BIND_IP>:8080/check
```

It tells the player in plain words whether their device uses the cache, and what to change if not. It works by looking up a few one-time names that only lanjaeger's DNS knows. Nothing else about the device or its browsing is recorded.

Tip: add a [DNS entry](README.md#dns-entries) such as `lanjaeger.lan` → `DNS_BIND_IP`, so players can type `http://lanjaeger.lan:8080/check`.

## On the day

- **Cables:** test every cable, label the switch ports by table, and cut the plugs off broken cables so they don't come back.
- **Error counters:** watch the core switch's per-port counters. FCS and alignment errors usually mean a bad cable; drops usually mean the link is too small.
- **Wi-Fi bridges:** if a MAC address shows up on two switch ports, someone is bridging Wi-Fi and wired.

## IPv6 DNS

Optional. Lets IPv6-only DNS lookups reach lanjaeger instead of the router.

1. Give the server a stable IPv6 address on the LAN (for example a ULA such as `fd00::10`).
2. In `.env`:
   ```
   DNS_BIND_IP6=fd00::10
   COMPOSE_FILE=docker-compose.yml:docker-compose.ipv6.yml
   ```
3. Run `docker compose up -d`.
4. Set the router's announced IPv6 DNS (RDNSS / DHCPv6) to that address.

Game downloads still go over IPv4: lancache-dns only answers IPv4 (A) records for cached domains, which keeps them on the cache. Docker relays these queries, so Blocky's statistics show the Docker gateway rather than the device for IPv6 clients.
