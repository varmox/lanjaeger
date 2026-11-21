# FortiGate configuration for lanjaeger

For [example network B](README.md#b-larger-party-fortigate-with-two-5g-uplinks): a FortiGate (written for a 100E) with SD-WAN over two 5G uplinks, a flat party network and lanjaeger as the cache and DNS server.

> **Before you paste anything:** the CLI below is written for FortiOS 7.0 / 7.2 and hasn't been run against a real FortiGate as part of this project. Option names change between FortiOS versions — if a line is rejected, look the setting up in the GUI under the name given in the heading. Interface names (`lan`, `wan1`, `wan2`), addresses and bandwidth numbers are examples: replace them with yours.

## What the FortiGate does here, and why

| Job | Why it matters for lanjaeger |
|---|---|
| DHCP with lanjaeger as the only DNS | Devices with a second DNS server skip the cache for about half their downloads |
| Block DNS that bypasses lanjaeger | Devices with hard-coded 8.8.8.8 or DNS-over-TLS would skip the cache and the ad blocker |
| SD-WAN: cache misses spread across both 5G links | Every cache miss comes from the **one** lanjaeger IP; "source IP" balancing would put them all on one link |
| SD-WAN: players stay on one link | Logins and anti-cheat can dislike a public IP that changes between connections |
| Traffic shaping | Big downloads must not push up everyone's ping |
| Nothing else | No SSL deep inspection on lanjaeger's traffic, no port forwarding to the server |

## Example values

| | |
|---|---|
| Party interface | `lan`, `192.168.10.1/23` |
| lanjaeger | `192.168.10.10` |
| DHCP pool | `192.168.10.100` – `192.168.11.250` |
| Uplinks | `wan1` (5G router 1), `wan2` (5G router 2), both DHCP |
| Measured speed (example) | wan1 300/50 Mbit/s, wan2 200/40 Mbit/s → 500/90 Mbit/s together |

**Measure the speeds at the venue**, at a busy time of day, with nothing else running. 5G speed changes with the time of day and the number of people around; the shaping below only works if its limits are **below** what the links really deliver.

**5G routers:** if they support bridge or IP passthrough mode, use it, so the FortiGate gets the public (or carrier) IP directly. Double NAT works, but consoles and some games then report a "strict" NAT type.

## 1. Addresses and services

```
config firewall address
    edit "lanjaeger"
        set subnet 192.168.10.10 255.255.255.255
    next
    edit "party-net"
        set subnet 192.168.10.0 255.255.254.0
    next
end

config firewall service custom
    edit "DNS-over-TLS"
        set tcp-portrange 853
    next
    edit "game-udp"
        set comment "Game and voice traffic; leaves out UDP 443 (QUIC, used by video and downloads)"
        set udp-portrange 1024-65535
    next
end
```

## 2. Party interface and DHCP (Network → Interfaces → lan)

```
config system interface
    edit "lan"
        set ip 192.168.10.1 255.255.254.0
        set allowaccess ping https ssh
    next
end

config system dhcp server
    edit 1
        set interface "lan"
        set default-gateway 192.168.10.1
        set netmask 255.255.254.0
        set dns-service specify
        set dns-server1 192.168.10.10
        set lease-time 28800
        config ip-range
            edit 1
                set start-ip 192.168.10.100
                set end-ip 192.168.11.250
            next
        end
        config reserved-address
            edit 1
                set ip 192.168.10.10
                set mac aa:bb:cc:dd:ee:ff
                set description "lanjaeger (or give it a static IP on the server)"
            next
        end
    next
end
```

- `dns-service specify` with **only** `dns-server1` is the important part. Don't use "Same as System DNS" or "Same as Interface IP".
- **IPv6:** leave IPv6 off on `lan` (no IPv6 address, no router advertisements). The 5G links probably have IPv6; as long as `lan` doesn't advertise it, players won't get the FortiGate's or the carrier's DNS over IPv6. If you want IPv6 for players anyway, the DNS server in the interface's IPv6 router advertisement settings must be lanjaeger's `DNS_BIND_IP6` (see [NETWORK.md](../NETWORK.md#ipv6-dns)).

## 3. SD-WAN (Network → SD-WAN)

```
config system sdwan
    set status enable
    set load-balance-mode source-ip-based
    config zone
        edit "virtual-wan-link"
        next
    end
    config members
        edit 1
            set interface "wan1"
            set zone "virtual-wan-link"
        next
        edit 2
            set interface "wan2"
            set zone "virtual-wan-link"
        next
    end
    config health-check
        edit "internet"
            set server "1.1.1.1" "8.8.8.8"
            set protocol ping
            set members 1 2
        next
    end
    config service
        edit 1
            set name "lanjaeger-both-links"
            set mode load-balance
            set hash-mode round-robin
            set src "lanjaeger"
            set dst "all"
            set priority-members 1 2
        next
    end
end
```

- **Default (`load-balance-mode source-ip-based`):** each player stays on one link.
- **Rule `lanjaeger-both-links`:** connections from lanjaeger alternate between both links, so cache misses and prefills use the full combined bandwidth. Game CDNs don't mind connections from different IPs.
- **Health check:** a dead 5G link is taken out of use automatically.
- With DHCP on `wan1`/`wan2`, the gateways come from the 5G routers. For static WAN addresses, add `set gateway <ip>` to each member.

## 4. Firewall policies (Policy & Objects → Firewall Policy)

Order matters: the first matching policy wins. If you add these to an existing configuration, move them into this order.

```
config firewall policy
    edit 0
        set name "lanjaeger to internet"
        set srcintf "lan"
        set dstintf "virtual-wan-link"
        set srcaddr "lanjaeger"
        set dstaddr "all"
        set action accept
        set schedule "always"
        set service "ALL"
        set nat enable
        set comments "Upstream DNS, cache misses, prefill. No SSL deep inspection on this policy."
    next
    edit 0
        set name "block DNS bypass"
        set srcintf "lan"
        set dstintf "virtual-wan-link"
        set srcaddr "party-net"
        set dstaddr "all"
        set action deny
        set schedule "always"
        set service "DNS" "DNS-over-TLS"
        set logtraffic all
        set comments "Players must use lanjaeger for DNS"
    next
    edit 0
        set name "players to internet"
        set srcintf "lan"
        set dstintf "virtual-wan-link"
        set srcaddr "party-net"
        set dstaddr "all"
        set action accept
        set schedule "always"
        set service "ALL"
        set nat enable
    next
end
```

- A device with hard-coded 8.8.8.8 then gets **no** DNS answers at all, rather than quietly skipping the cache. The player notices and the [check page](../README.md#checking-it-works) tells them how to fix it. The "block DNS bypass" policy's log shows which devices are affected.
- **DNS over HTTPS** in browsers can't be caught by port, because it looks like normal HTTPS. If you use application control on "players to internet", block the DNS-over-HTTPS signatures there (search for "DoH" / "DNS over HTTPS" in the application list). Otherwise the check page tells affected players how to turn it off in their browser.
- **Security profiles:** keep "lanjaeger to internet" without SSL deep inspection. On the 100E, every profile you add to "players to internet" lowers throughput; with two 5G links that's usually fine, but check with the load test.
- **No VIP / port forwarding** to lanjaeger. Its web UI must never be reachable from the internet.

## 5. Traffic shaping (Policy & Objects → Traffic Shaping)

The plan:

1. **Game and voice traffic** (UDP from 1024 up) gets high priority.
2. **lanjaeger** (all cache misses and prefills) gets low priority and a **cap** at about 70% of the combined download speed, so online games always have headroom.
3. **Each player** gets a per-IP cap, so one person downloading something that isn't cached can't take the whole line.

First, tell the FortiGate how fast the uplinks really are (about 90% of the measured speed, in kbit/s). Without these, priorities have no effect:

```
config system interface
    edit "wan1"
        set inbandwidth 270000
        set outbandwidth 45000
    next
    edit "wan2"
        set inbandwidth 180000
        set outbandwidth 36000
    next
end
```

Shapers:

```
config firewall shaper traffic-shaper
    edit "realtime"
        set bandwidth-unit mbps
        set guaranteed-bandwidth 20
        set maximum-bandwidth 450
        set priority high
    next
    edit "lanjaeger-fill"
        set bandwidth-unit mbps
        set maximum-bandwidth 350
        set priority low
    next
end

config firewall shaper per-ip-shaper
    edit "per-player"
        set bandwidth-unit mbps
        set max-bandwidth 100
    next
end
```

Shaping policies, again first match wins:

```
config firewall shaping-policy
    edit 1
        set name "games and voice"
        set srcintf "lan"
        set dstintf "virtual-wan-link"
        set srcaddr "party-net"
        set dstaddr "all"
        set service "game-udp"
        set traffic-shaper "realtime"
        set traffic-shaper-reverse "realtime"
    next
    edit 2
        set name "lanjaeger cache fill"
        set srcintf "lan"
        set dstintf "virtual-wan-link"
        set srcaddr "lanjaeger"
        set dstaddr "all"
        set service "ALL"
        set traffic-shaper "lanjaeger-fill"
        set traffic-shaper-reverse "lanjaeger-fill"
    next
    edit 3
        set name "per player"
        set srcintf "lan"
        set dstintf "virtual-wan-link"
        set srcaddr "party-net"
        set dstaddr "all"
        set service "ALL"
        set per-ip-shaper "per-player"
    next
end
```

- **`traffic-shaper-reverse` is the one that limits downloads.** `traffic-shaper` applies in the direction the connection was opened (uploads); the reply direction is the download.
- The `lanjaeger-fill` cap applies only to internet traffic (`dstintf virtual-wan-link`). Players downloading **from** the cache go through the switch and are never limited.
- **Prefill at night uncapped:** create a recurring schedule for the party hours and set it on the "lanjaeger cache fill" shaping policy (`set schedule "<name>"`). Outside those hours the cap doesn't apply, so a nightly prefill gets the full bandwidth.
- If ping still rises under load, lower `inbandwidth`, `outbandwidth` and the caps by 10–20%. 5G bandwidth varies, and a limit that is too high lets the queue fill up in the 5G router, out of the FortiGate's control.

## 6. Check that it works

From the FortiGate CLI:

```
# both 5G links up and healthy
diagnose sys sdwan member
diagnose sys sdwan health-check

# which links lanjaeger's connections use (should be both)
diagnose sys session filter clear
diagnose sys session filter src 192.168.10.10
diagnose sys session list | grep -E 'dev=|gwy='

# shapers in use, with current rates and drops
diagnose firewall shaper traffic-shaper list
diagnose firewall shaper per-ip-shaper list
```

Then from the party network, as part of the [test plan](../SETUP.md#2-test-plan):

| # | Step | Expected |
|---|---|---|
| FG1 | A player PC: `ipconfig /all` | DNS server `192.168.10.10` only, no IPv6 DNS |
| FG2 | `nslookup example.com 8.8.8.8` on a player PC | times out (blocked); "block DNS bypass" log shows the attempt |
| FG3 | Open `/check` on a player PC | "Yes" |
| FG4 | Prefill or install an uncached game, watch the WAN interfaces in the dashboard | traffic on **both** wan1 and wan2, total near the `lanjaeger-fill` cap |
| FG5 | Same time: ping test or online game on another PC | ping stays stable (no more than about 20–30 ms higher) |
| FG6 | Unplug one 5G router | health check marks it dead within a few seconds; traffic continues on the other |
| FG7 | From outside (phone on mobile data) try the public IP on 8080 | not reachable |
