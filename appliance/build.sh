#!/usr/bin/env bash
# Build the lanjaeger appliance OVA (Photon OS 5). See appliance/README.md.
#
#   appliance/build.sh            → appliance/out/lanjaeger-<version>.ova
#
# Needs Linux with Docker that can run privileged containers (loop devices),
# git, and about 25 GB of free disk space. Uses photon-os-installer's
# container (create-image, create-ova) — no hypervisor needed.
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/.." && pwd)
POI_VERSION=${POI_VERSION:-v2.9}
VERSION=${VERSION:-$(git -C "$REPO" describe --tags --always 2>/dev/null || echo dev)}
OUT=${OUT:-$HERE/out}
WORK=$OUT/work
POI_IMAGE=lanjaeger-poi:$POI_VERSION

say() { printf '\n==> %s\n' "$*"; }

rm -rf "$WORK"
mkdir -p "$WORK/images" "$WORK/repo" "$WORK/files"

say "Photon OS installer container ($POI_VERSION)"
if ! docker image inspect "$POI_IMAGE" >/dev/null 2>&1; then
	git clone -q --depth 1 --branch "$POI_VERSION" https://github.com/vmware/photon-os-installer "$WORK/poi-src"
	docker buildx build --load -t "$POI_IMAGE" --build-context poi-helper="$WORK/poi-src" "$WORK/poi-src/docker"
fi

say "lanjaeger source"
git clone -q --depth 1 "file://$REPO" "$WORK/lanjaeger"
git -C "$WORK/lanjaeger" remote set-url origin https://github.com/varmox/lanjaeger.git
rm -rf "$WORK/lanjaeger/appliance/out"

say "Docker images (pulled/built here, loaded into the appliance)"
cd "$WORK/lanjaeger"
cp .env.example .env          # compose needs one to resolve the image names
images=$(COMPOSE_PROFILES=webui,monitoring docker compose config --images | sort -u)
prefill=$(grep -oE '"tpill90/[a-z-]+:v[0-9.]+"' webui/server/lanjaeger_webui/prefill.py | tr -d '"')
COMPOSE_PROFILES=webui docker compose build webui
rm .env
: > "$WORK/docker-images.yaml"
for img in $images $prefill; do
	[ "$img" = lanjaeger-webui:local ] || docker pull -q "$img"
	tar="$(echo "$img" | tr '/:@' '___').tar"
	docker save -o "$WORK/images/$tar" "$img"
	printf '        - method: load\n          filename: images/%s\n' "$tar" >> "$WORK/docker-images.yaml"
	echo "  $img"
done
cd "$HERE"

say "Kickstart"
cp packages_lanjaeger.json lanjaeger_ova.yaml "$WORK/"
cp files/* "$WORK/files/"
{ cat lanjaeger_ks.yaml; printf '\ndocker:\n    images:\n'; cat "$WORK/docker-images.yaml"; } > "$WORK/lanjaeger_ks.yaml"

poi() {
	docker run --rm --privileged -v /dev:/dev -v "$WORK:/workdir" -v "$WORK/repo:/repo" -w /workdir "$POI_IMAGE" "$@"
}

say "Installing Photon OS into the disk image"
poi create-image -c lanjaeger_ks.yaml -v 5.0

say "Packaging the OVA"
poi create-ova --installer-config lanjaeger_ks.yaml --ova-config lanjaeger_ova.yaml \
	--ova-name "lanjaeger-$VERSION" --param "version=$VERSION" --mf

mv "$WORK/lanjaeger-$VERSION.ova" "$OUT/"
(cd "$OUT" && sha256sum "lanjaeger-$VERSION.ova" > "lanjaeger-$VERSION.ova.sha256")
say "Done: $OUT/lanjaeger-$VERSION.ova ($(du -h "$OUT/lanjaeger-$VERSION.ova" | cut -f1))"
