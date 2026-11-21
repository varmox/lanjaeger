# lanjaeger appliance (OVA)

A ready-to-deploy virtual machine for VMware vSphere/ESXi. It's Photon OS 5 with Docker and the whole lanjaeger stack preloaded: lancache, DNS with ad blocking, the web UI and, optionally, Telegraf, Prometheus and Grafana. You set the network, passwords and options in the deployment wizard. After power-on, the console shows the web UI address.

**Download:** [Releases](https://github.com/varmox/lanjaeger/releases) (`lanjaeger-<version>.ova` and its `.sha256`). Builds from `main` are under [Actions → Appliance](https://github.com/varmox/lanjaeger/actions/workflows/appliance.yml) → artifact `lanjaeger-ova`.

## What's inside

| | |
|---|---|
| OS | Photon OS 5, generic kernel, EFI, VMware Tools (open-vm-tools) |
| Hardware | vmx-14 (ESXi 6.7+), PVSCSI, vmxnet3 |
| Disk 1 | 40 GB system disk: OS, Docker, `/opt/lanjaeger` |
| Disk 2 | 500 GB empty, thin. The game cache, formatted as XFS on first boot and mounted at `/srv/lancache` |
| Sizes | Small (4 vCPU, 8 GB), **Medium** (8 vCPU, 16 GB, default), Large (16 vCPU, 32 GB) |
| Preloaded images | monolithic, lancache-dns, Blocky, web UI, log rotation, Telegraf, Prometheus, Grafana, Steam/Epic/Battle.net prefill |

## Deploy in vSphere

1. **Deploy OVF Template** → upload the `.ova` → name and folder → compute resource.
2. **Configuration:** Small, Medium or Large (see the table above).
3. **Storage:** thin provisioning is fine; put it on the fastest datastore (SSD/NVMe).
4. **Networks:** map `LAN` to the port group your players are on.
5. **Customize template.** All options are optional, but set at least the network:

   | Section | Option | Default |
   |---|---|---|
   | 1. Appliance | Hostname | `lanjaeger` |
   | | Root password | empty = `lanjaeger`, must be changed at first login |
   | | Enable SSH | off |
   | | Time zone | `UTC` (e.g. `Europe/Zurich`) |
   | 2. Network | IP address | empty = DHCP. **Use a static IP**, e.g. `192.168.10.10` |
   | | Prefix length | `24` |
   | | Gateway | — |
   | | DNS servers | `1.1.1.1 9.9.9.9` (for the appliance itself) |
   | | Search domain, NTP servers | —, `pool.ntp.org` |
   | 3. Cache and DNS | Upstream DNS for players | empty = the DNS servers above |
   | | Cache size limit (GB) | empty = 90% of disk 2 |
   | | Ad blocking | on |
   | 4. Web UI | Web UI password | empty = random, shown on the console |
   | | Public dashboard | off |
   | 5. Monitoring | Enable Grafana | off |
   | | Grafana admin password | empty = web UI password |
   | | Metrics token | empty = generated when Grafana is on |

6. **Before the first power-on:** *Edit Settings → Hard disk 2* and set the cache size you want (e.g. 2 TB). You can also enlarge it later; the appliance grows the filesystem at the next boot.
7. **Power on.** The first boot takes 1–3 minutes: it formats the cache disk and starts the containers. The VM console then shows:

   ```
   lanjaeger appliance (Photon OS 5)

   Web UI ............ http://192.168.10.10:8080
   Client check ...... http://192.168.10.10:8080/check
   Grafana ........... http://192.168.10.10:3000   (user admin)
   DNS for players ... 192.168.10.10   (hand this out as the only DNS server via DHCP)
   ```

8. Point your router's DHCP at the appliance as the **only** DNS server, then go through [NETWORK.md](../NETWORK.md) and the test plan in [SETUP.md](../SETUP.md).

**Changing options later:** power off, change them in *VM → Configure → vApp Options*, power on. Network, passwords, SSH and time zone are re-applied at every boot. Values you've changed in the web UI's Settings page stay as they are, unless you change the same option in vSphere.

**With ovftool** (also for standalone ESXi without vCenter, which has no vApp options wizard):

```bash
ovftool --acceptAllEulas --name=lanjaeger --deploymentOption=medium --diskMode=thin \
  --net:LAN="VM Network" --datastore=ssd01 \
  --prop:lanjaeger.ip=192.168.10.10 --prop:lanjaeger.prefix=24 --prop:lanjaeger.gateway=192.168.10.1 \
  --prop:lanjaeger.dns="192.168.10.1" --prop:lanjaeger.webui_password='…' --prop:lanjaeger.root_password='…' \
  --prop:lanjaeger.timezone=Europe/Zurich --prop:lanjaeger.monitoring=True \
  --X:injectOvfEnv --powerOn \
  lanjaeger-<version>.ova vi://root@esxi01/
```

Standalone ESXi has no vApp options: `--X:injectOvfEnv --powerOn` passes the options to the VM on its first start anyway. Without it, or after deploying through the ESXi host client, the appliance comes up with DHCP and defaults. Then log in on the console, write the options to `/etc/lanjaeger/ovf-env.xml` (same format as in [test-boot.sh](test-boot.sh)), and reboot.

## Operating it

- **Console / SSH:** `root`. The stack lives in `/opt/lanjaeger`, and its `.env` is managed by the appliance and the web UI.
- **Logs of the setup:** `journalctl -b -u lanjaeger-setup-early -u lanjaeger-setup-late -u lanjaeger`
- **Update lanjaeger:** web UI → Services → Update (new images), or `cd /opt/lanjaeger && git pull && ./update.sh`.
- **Update Photon OS:** `tdnf update -y && reboot`.
- **The cache disk filled up / grew:** enlarge *Hard disk 2* in vSphere and reboot. With "Cache size limit" empty, the limit follows the disk size.

## Build it yourself

On Linux with Docker (privileged containers) and about 25 GB free:

```bash
appliance/build.sh                                     # → appliance/out/lanjaeger-<version>.ova
appliance/test-boot.sh appliance/out/work/lanjaeger-root.img   # boot test in QEMU
```

The build uses VMware's [photon-os-installer](https://github.com/vmware/photon-os-installer) container to install Photon OS into a disk image, and [open-vmdk](https://github.com/vmware/open-vmdk)'s `ova-compose` to package the OVA. No hypervisor is needed for the build.

| File | Purpose |
|---|---|
| `lanjaeger_ks.yaml` | Photon OS installation: disks, packages, files, services |
| `packages_lanjaeger.json` | Package list |
| `lanjaeger_ova.yaml` | The OVA: hardware, sizes, deployment options |
| `files/lanjaeger-setup` | Applies the options at boot (network, disk, `.env`, …) |
| `files/*.service` | systemd units that run it, and the stack |
