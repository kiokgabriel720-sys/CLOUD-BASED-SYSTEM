#!/usr/bin/env python3
"""Lab simulator: emulates several network devices pushing metrics to CloudNMS.

Used for testing and demonstration when physical routers/switches are not available.
Config file format (JSON):
    [{"name": "Core Router", "key": "<api key>", "profile": "router"}, ...]
Run with --fault "Core Router:cpu" (or "Web Server:memory=86") to force a fault on one device.
"""
import argparse
import json
import random
import time

import requests

PROFILES = {  # typical baseline (mean, spread) per metric
    "router":   {"cpu_usage": (35, 8), "memory_usage": (48, 4), "latency_ms": (12, 4), "bandwidth_usage": (45, 12)},
    "switch":   {"cpu_usage": (18, 5), "memory_usage": (35, 3), "latency_ms": (3, 1), "bandwidth_usage": (38, 10)},
    "firewall": {"cpu_usage": (42, 9), "memory_usage": (55, 5), "latency_ms": (6, 2), "bandwidth_usage": (40, 10)},
    "server":   {"cpu_usage": (30, 10), "memory_usage": (62, 6), "latency_ms": (4, 1.5), "bandwidth_usage": (22, 8)},
    "ap":       {"cpu_usage": (25, 7), "memory_usage": (40, 4), "latency_ms": (28, 10), "bandwidth_usage": (30, 12)},
}
FAULTS = {"cpu": ("cpu_usage", 94), "memory": ("memory_usage", 95),
          "latency": ("latency_ms", 320), "bandwidth": ("bandwidth_usage", 97)}


def sample(profile, fault=None):
    data = {k: max(0.0, round(random.gauss(m, s), 2)) for k, (m, s) in PROFILES[profile].items()}
    for k in ("cpu_usage", "memory_usage", "bandwidth_usage"):
        data[k] = min(data[k], 100.0)
    data["packet_loss"] = round(max(0.0, random.gauss(0.2, 0.3)), 2)
    data["bandwidth_mbps"] = round(data["bandwidth_usage"] * 10, 1)  # 1 Gbps links
    if fault:
        kind, _, custom = fault.partition("=")
        field, value = FAULTS[kind]
        data[field] = float(custom) + round(random.uniform(-0.8, 0.8), 1) if custom else value
    return data


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--server", default="http://127.0.0.1:5000")
    p.add_argument("--config", required=True)
    p.add_argument("--interval", type=int, default=30)
    p.add_argument("--fault", action="append", default=[], help="'Device name:type[=value]'")
    p.add_argument("--rounds", type=int, default=0, help="0 = run forever")
    args = p.parse_args()
    devices = json.load(open(args.config))
    faults = dict(f.split(":", 1) for f in args.fault)
    n = 0
    while True:
        for d in devices:
            payload = sample(d["profile"], faults.get(d["name"]))
            r = requests.post(args.server.rstrip("/") + "/api/v1/metrics", json=payload,
                              headers={"X-API-Key": d["key"]}, timeout=10)
            print(f'{d["name"]:<24} {r.status_code} {r.json().get("device_status", r.text)}')
        n += 1
        if args.rounds and n >= args.rounds:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
