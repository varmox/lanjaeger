#!/usr/bin/env bash
# Boot the built appliance disk in QEMU with deployment options passed like
# vSphere's ISO transport (ovf-env.xml on a CD), then check the stack works.
#
#   appliance/test-boot.sh appliance/out/work/lanjaeger-root.img
#
# Needs qemu-system-x86_64, OVMF, genisoimage, dig, curl; uses KVM if available.
set -euo pipefail

ROOT_IMG=$(realpath "$1")
T=$(mktemp -d)
trap 'kill "$(cat "$T/qemu.pid" 2>/dev/null)" 2>/dev/null || true' EXIT

# QEMU's user network: guest 10.0.2.15, gateway 10.0.2.2, DNS 10.0.2.3.
cat > "$T/ovf-env.xml" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<Environment xmlns="http://schemas.dmtf.org/ovf/environment/1"
             xmlns:oe="http://schemas.dmtf.org/ovf/environment/1"
             oe:id="lanjaeger">
  <PropertySection>
    <Property oe:key="lanjaeger.hostname" oe:value="lj-test"/>
    <Property oe:key="lanjaeger.root_password" oe:value="Test-root-1"/>
    <Property oe:key="lanjaeger.ssh" oe:value="True"/>
    <Property oe:key="lanjaeger.timezone" oe:value="Europe/Zurich"/>
    <Property oe:key="lanjaeger.ip" oe:value="10.0.2.15"/>
    <Property oe:key="lanjaeger.prefix" oe:value="24"/>
    <Property oe:key="lanjaeger.gateway" oe:value="10.0.2.2"/>
    <Property oe:key="lanjaeger.dns" oe:value="10.0.2.3"/>
    <Property oe:key="lanjaeger.webui_password" oe:value="ci-webui"/>
    <Property oe:key="lanjaeger.monitoring" oe:value="True"/>
    <Property oe:key="lanjaeger.metrics_token" oe:value="ci-token"/>
  </PropertySection>
</Environment>
EOF
genisoimage -quiet -r -V "OVF ENV" -o "$T/ovf-env.iso" "$T/ovf-env.xml"

qemu-img create -q -f qcow2 -F raw -b "$ROOT_IMG" "$T/root.qcow2"
qemu-img create -q -f qcow2 "$T/cache.qcow2" 30G
OVMF=$(ls /usr/share/OVMF/OVMF_CODE_4M.fd /usr/share/OVMF/OVMF_CODE.fd /usr/share/ovmf/OVMF.fd 2>/dev/null | head -1)
VARS=$(ls /usr/share/OVMF/OVMF_VARS_4M.fd /usr/share/OVMF/OVMF_VARS.fd 2>/dev/null | head -1)
cp "$VARS" "$T/vars.fd"
ACCEL=tcg; [ -w /dev/kvm ] && ACCEL=kvm
echo "booting with $ACCEL"

qemu-system-x86_64 -machine q35,accel=$ACCEL -cpu max -smp 4 -m 8192 \
	-drive if=pflash,format=raw,readonly=on,file="$OVMF" -drive if=pflash,format=raw,file="$T/vars.fd" \
	-drive file="$T/root.qcow2",if=virtio -drive file="$T/cache.qcow2",if=virtio \
	-cdrom "$T/ovf-env.iso" \
	-netdev user,id=n0,hostfwd=tcp::18080-:8080,hostfwd=tcp::13000-:3000,hostfwd=tcp::12222-:22,hostfwd=udp::15353-:53 \
	-device virtio-net-pci,netdev=n0 \
	-display none -serial file:"$T/serial.log" -daemonize -pidfile "$T/qemu.pid"

ssh_vm() {
	sshpass -p Test-root-1 ssh -q -p 12222 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null root@127.0.0.1 "$@"
}

echo "waiting for the web UI (first boot formats the cache disk and starts all containers)"
for i in $(seq 1 120); do
	curl -fsS -m 5 http://127.0.0.1:18080/api/session >/dev/null 2>&1 && break
	[ "$i" = 120 ] && { echo "web UI never came up"; ssh_vm 'journalctl -b -u "lanjaeger*" --no-pager | tail -80; docker ps -a' || tail -50 "$T/serial.log"; exit 1; }
	sleep 10
done

echo "--- deployment options applied"
ssh_vm 'hostname; ip -4 -o addr show scope global; readlink /etc/localtime; findmnt -n /srv/lancache; grep -E "^(LANCACHE_IP|DNS_BIND_IP|UPSTREAM_DNS|CACHE_ROOT|CACHE_DISK_SIZE|COMPOSE_PROFILES|TZ)=" /opt/lanjaeger/.env' | tee "$T/vm.txt"
grep -qx lj-test "$T/vm.txt"
grep -q 'inet 10.0.2.15/24' "$T/vm.txt"
grep -q 'Europe/Zurich' "$T/vm.txt"
grep -q '/srv/lancache /dev/vdb xfs' "$T/vm.txt"
grep -qx 'LANCACHE_IP=10.0.2.15' "$T/vm.txt"
grep -qx 'UPSTREAM_DNS=10.0.2.3' "$T/vm.txt"
grep -qx 'COMPOSE_PROFILES=webui,monitoring' "$T/vm.txt"

echo "--- containers"
ssh_vm 'docker ps --format "{{.Names}}\t{{.Status}}"'
for i in $(seq 1 30); do
	ssh_vm 'docker ps --format "{{.Status}}"' | grep -q -E 'starting|unhealthy' || break
	sleep 10
done

echo "--- DNS: game domain resolves to the cache"
dig +short -p 15353 @127.0.0.1 lancache.steamcontent.com | tee /dev/stderr | grep -qx 10.0.2.15

echo "--- web UI sign-in with the deployment password"
curl -fsS -X POST -H 'X-Lanjaeger: 1' -H 'Content-Type: application/json' -d '{"password":"ci-webui"}' \
	http://127.0.0.1:18080/api/login | grep -q signedIn

echo "--- metrics and Grafana"
curl -fsS -H 'Authorization: Bearer ci-token' http://127.0.0.1:18080/metrics | grep -E '^lanjaeger_(up|dns_up|containers_unhealthy) '
for i in $(seq 1 30); do curl -fsS http://127.0.0.1:13000/api/health >/dev/null 2>&1 && break; sleep 5; done
curl -fsS http://127.0.0.1:13000/api/health

echo "--- second boot keeps settings and the cache"
ssh_vm 'touch /srv/lancache/keep-me; systemctl reboot' || true
sleep 20
for i in $(seq 1 60); do curl -fsS -m 5 http://127.0.0.1:18080/api/session >/dev/null 2>&1 && break; sleep 10; done
ssh_vm 'test -f /srv/lancache/keep-me && echo cache kept; systemctl is-failed lanjaeger-setup-early lanjaeger-setup-late lanjaeger || true'
ssh_vm 'test -f /srv/lancache/keep-me'

echo "appliance OK"
