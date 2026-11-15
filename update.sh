#!/usr/bin/env bash
# Pull the latest images, recreate changed containers, drop old images.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -f .env ]; then
	echo "No .env found — run: cp .env.example .env  and edit it first." >&2
	exit 1
fi

docker compose pull
docker compose up -d --remove-orphans
docker image prune -f
docker compose ps
