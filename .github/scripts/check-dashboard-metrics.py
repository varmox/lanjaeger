"""Every metric the Grafana dashboard queries must exist in Prometheus.

    python3 check-dashboard-metrics.py <dashboard.json> <prometheus url> [allowed-missing ...]
"""
import json
import re
import sys
import urllib.request

PREFIXES = ("lanjaeger_", "blocky_", "net_", "cpu_", "mem_", "diskio_", "docker_")

dashboard, prom, *allowed = sys.argv[1:]
exprs = [t["expr"] for p in json.load(open(dashboard))["panels"] for t in p.get("targets", [])]
wanted = {m for e in exprs for m in re.findall(r"\b([a-z][a-z0-9_]*)\b", e) if m.startswith(PREFIXES)}
with urllib.request.urlopen(prom + "/api/v1/label/__name__/values", timeout=10) as r:
    have = set(json.load(r)["data"])
missing = sorted(wanted - have - set(allowed))
print(f"{len(wanted)} metrics used by the dashboard, {len(wanted & have)} present")
if missing:
    sys.exit("missing in Prometheus: " + ", ".join(missing))
