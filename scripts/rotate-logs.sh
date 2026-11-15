#!/bin/sh
# Size-based rotation for the lancache logs, run by the `logrotate` service.
#
# Every LOG_CHECK_INTERVAL seconds, each *.log in /logs larger than
# LOG_MAX_SIZE_MB is compressed to <name>.1.gz and truncated in place
# ("copytruncate": nginx keeps writing to the same file, no restart needed).
# Older copies shift to .2.gz … .LOG_KEEP.gz; anything beyond is deleted.
# Lines written during the copy itself can be lost — fine for access logs.
set -eu

LOG_DIR=${LOG_DIR:-/logs}
LOG_MAX_SIZE_MB=${LOG_MAX_SIZE_MB:-1024}
LOG_KEEP=${LOG_KEEP:-7}
LOG_CHECK_INTERVAL=${LOG_CHECK_INTERVAL:-3600}

max_bytes=$((LOG_MAX_SIZE_MB * 1024 * 1024))

rotate() {
	f=$1
	rm -f "$f.$LOG_KEEP.gz"
	i=$LOG_KEEP
	while [ "$i" -gt 1 ]; do
		prev=$((i - 1))
		if [ -f "$f.$prev.gz" ]; then mv "$f.$prev.gz" "$f.$i.gz"; fi
		i=$prev
	done
	gzip -c "$f" > "$f.1.gz.tmp" && mv "$f.1.gz.tmp" "$f.1.gz"
	: > "$f"
}

echo "logrotate: ${LOG_DIR}/*.log, rotate above ${LOG_MAX_SIZE_MB} MB, keep ${LOG_KEEP}, check every ${LOG_CHECK_INTERVAL}s"
while true; do
	for f in "$LOG_DIR"/*.log; do
		[ -f "$f" ] || continue
		size=$(stat -c %s "$f")
		if [ "$size" -gt "$max_bytes" ]; then
			echo "logrotate: rotating $f ($((size / 1024 / 1024)) MB)"
			rotate "$f"
		fi
	done
	sleep "$LOG_CHECK_INTERVAL"
done
