#!/usr/bin/env python3
"""CloudNMS monitoring agent.

Runs on (or next to) a managed device, collects performance metrics and pushes them
to the cloud server over HTTPS every INTERVAL seconds.  Because the agent initiates
the connection, no inbound firewall ports need to be opened on the local network.

Usage:
    python nms_agent.py --server https://<account>.pythonanywhere.com \
        --key <device API key> --target 8.8.8.8 --link-mbps 100 --interval 60
"""
import argparse
import socket
import sys
import time

import psutil
import requests


def tcp_latency(host, port, attempts=4, timeout=2.0):
    """Measure round-trip latency with TCP handshakes; return (avg_ms, loss_%)."""
    samples = []
    for _ in range(attempts):
        start = time.perf_counter()
        try:
            with socket.create_connection((host, port), timeout=timeout):
                samples.append((time.perf_counter() - start) * 1000)
        except OSError:
            pass
        time.sleep(0.2)
    loss = 100 * (attempts - len(samples)) / attempts
    avg = sum(samples) / len(samples) if samples else None
    return avg, loss


def collect(args, prev):
    """Collect one sample. ``prev`` holds the previous network counters."""
    cpu = psutil.cpu_percent(interval=1)
    mem = psutil.virtual_memory().percent
    counters = psutil.net_io_counters()
    now = time.time()
    mbps = util = None
    if prev:
        elapsed = now - prev["t"]
        bits = ((counters.bytes_sent - prev["tx"]) + (counters.bytes_recv - prev["rx"])) * 8
        mbps = bits / elapsed / 1_000_000
        util = min(100.0, 100 * mbps / args.link_mbps)
    latency, loss = tcp_latency(args.target, args.target_port)
    sample = {"cpu_usage": cpu, "memory_usage": mem, "packet_loss": loss}
    if latency is not None:
        sample["latency_ms"] = round(latency, 2)
    if mbps is not None:
        sample["bandwidth_mbps"] = round(mbps, 3)
        sample["bandwidth_usage"] = round(util, 2)
    return sample, {"t": now, "tx": counters.bytes_sent, "rx": counters.bytes_recv}


def main():
    p = argparse.ArgumentParser(description="CloudNMS monitoring agent")
    p.add_argument("--server", required=True, help="Base URL of the CloudNMS server")
    p.add_argument("--key", required=True, help="Device API key from the device page")
    p.add_argument("--target", default="8.8.8.8", help="Host used for latency checks")
    p.add_argument("--target-port", type=int, default=53)
    p.add_argument("--link-mbps", type=float, default=100.0, help="Link capacity in Mbps")
    p.add_argument("--interval", type=int, default=60, help="Seconds between samples")
    p.add_argument("--once", action="store_true", help="Send one sample and exit")
    args = p.parse_args()

    url = args.server.rstrip("/") + "/api/v1/metrics"
    prev = None
    while True:
        sample, prev = collect(args, prev)
        try:
            r = requests.post(url, json=sample, headers={"X-API-Key": args.key}, timeout=10)
            print(time.strftime("%H:%M:%S"), r.status_code, r.json())
            if r.status_code == 401:
                sys.exit("API key rejected - check the key on the device page.")
        except requests.RequestException as exc:
            # Network failure: keep running; the server will mark the device offline
            print(time.strftime("%H:%M:%S"), "send failed:", exc)
        if args.once:
            break
        time.sleep(max(1, args.interval - 2))


if __name__ == "__main__":
    main()
