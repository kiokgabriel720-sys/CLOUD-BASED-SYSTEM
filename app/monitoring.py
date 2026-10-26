"""Monitoring engine: metric ingestion, threshold evaluation, fault detection and alerts."""
import socket
import time
from datetime import timedelta

from flask import current_app

from . import db
from .models import Alert, Device, Metric, get_thresholds, utcnow

# metric field -> (threshold prefix, label, unit)
CHECKS = {
    "cpu_usage": ("cpu", "CPU utilisation", "%"),
    "memory_usage": ("memory", "Memory utilisation", "%"),
    "latency_ms": ("latency", "Latency", " ms"),
    "bandwidth_usage": ("bandwidth", "Link utilisation", "%"),
    "packet_loss": ("packet_loss", "Packet loss", "%"),
}
HYSTERESIS = 0.15  # an alert clears only when the value is 15% below the warning level
LIMITS = {  # valid ranges used for server-side validation
    "cpu_usage": (0, 100), "memory_usage": (0, 100), "bandwidth_usage": (0, 100),
    "packet_loss": (0, 100), "latency_ms": (0, 60000), "bandwidth_mbps": (0, 400000),
}


class MetricValidationError(ValueError):
    pass


def validate_payload(payload):
    """Return a clean dict of metric values or raise MetricValidationError."""
    if not isinstance(payload, dict):
        raise MetricValidationError("JSON object expected")
    clean = {}
    for field, (lo, hi) in LIMITS.items():
        if field in payload and payload[field] is not None:
            try:
                value = float(payload[field])
            except (TypeError, ValueError):
                raise MetricValidationError(f"{field} must be numeric")
            if not lo <= value <= hi:
                raise MetricValidationError(f"{field} must be between {lo} and {hi}")
            clean[field] = round(value, 2)
    if not clean:
        raise MetricValidationError("no metric values supplied")
    return clean


def _severity(value, warn, crit):
    if value is None:
        return None
    if value >= crit:
        return "critical"
    if value >= warn:
        return "warning"
    return None


def _open_alert(device, alert_type):
    return (Alert.query.filter_by(device_id=device.id, alert_type=alert_type)
            .filter(Alert.status != "resolved").first())


def raise_or_update_alert(device, alert_type, severity, message, now):
    """Create an alert once per fault; escalate severity instead of duplicating."""
    alert = _open_alert(device, alert_type)
    if alert is None:
        alert = Alert(device_id=device.id, alert_type=alert_type, severity=severity,
                      message=message, created_at=now)
        db.session.add(alert)
        return alert, True
    if alert.severity != severity:
        alert.severity = severity
        alert.message = message
    return alert, False


def resolve_alert(device, alert_type, now):
    alert = _open_alert(device, alert_type)
    if alert:
        alert.status = "resolved"
        alert.resolved_at = now
    return alert


def evaluate_metric(device, metric, now=None, thresholds=None, previous=None):
    """Compare a metric sample with the configured thresholds.

    * critical values raise an alert immediately;
    * warning values must persist for two consecutive samples (debounce);
    * an alert clears only when the value falls below the hysteresis band.
    """
    now = now or utcnow()
    t = thresholds or get_thresholds()
    faults = 0
    for field, (prefix, label, unit) in CHECKS.items():
        value = getattr(metric, field)
        if value is None:
            # metric not reported in this sample: keep the previous state
            if _open_alert(device, prefix):
                faults += 1
            continue
        sev = _severity(value, t[f"{prefix}_warning"], t[f"{prefix}_critical"])
        if sev == "warning" and not _open_alert(device, prefix):
            prev_value = getattr(previous, field, None) if previous else None
            if prev_value is None or prev_value < t[f"{prefix}_warning"]:
                continue  # single transient spike - wait for confirmation
        if sev:
            faults += 1
            limit = t[f"{prefix}_{sev}"]
            msg = f"{label} on {device.name} is {value:g}{unit} (threshold {limit:g}{unit})"
            raise_or_update_alert(device, prefix, sev, msg, now)
        elif value < t[f"{prefix}_warning"] * (1 - HYSTERESIS):
            resolve_alert(device, prefix, now)
        elif _open_alert(device, prefix):
            faults += 1  # inside the hysteresis band: keep the alert open (no flapping)
    device.status = "warning" if faults else "online"
    return faults


def ingest_metrics(device, payload, now=None, thresholds=None):
    """Store a validated metric sample and run fault detection."""
    now = now or utcnow()
    values = validate_payload(payload)
    previous = device.metrics.order_by(Metric.recorded_at.desc()).first()
    metric = Metric(device_id=device.id, recorded_at=now, **values)
    db.session.add(metric)
    device.last_seen = now
    if resolve_alert(device, "offline", now):
        pass  # device has recovered
    evaluate_metric(device, metric, now, thresholds, previous)
    return metric


def check_heartbeats(now=None, timeout=None):
    """Mark devices offline when no data has arrived within the heartbeat timeout."""
    now = now or utcnow()
    timeout = timeout or current_app.config["HEARTBEAT_TIMEOUT_SECONDS"]
    cutoff = now - timedelta(seconds=timeout)
    newly_offline = []
    for device in Device.query.filter_by(monitored=True).all():
        reference = device.last_seen or device.created_at
        if reference < cutoff and device.status != "offline":
            device.status = "offline"
            seen = device.last_seen.strftime("%H:%M UTC") if device.last_seen else "never"
            raise_or_update_alert(device, "offline", "critical",
                                  f"{device.name} ({device.ip_address}) is unreachable - "
                                  f"no heartbeat since {seen}", now)
            newly_offline.append(device)
    return newly_offline


def tcp_probe(host, port, timeout=2.0):
    """Active reachability check. Returns latency in ms, or None if unreachable."""
    start = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return round((time.perf_counter() - start) * 1000, 2)
    except OSError:
        return None


def poll_device(device, now=None):
    """On-demand poll from the cloud server to the device management port."""
    now = now or utcnow()
    latency = tcp_probe(device.ip_address, device.mgmt_port or 22)
    if latency is None:
        device.status = "offline"
        raise_or_update_alert(device, "offline", "critical",
                              f"{device.name} ({device.ip_address}) did not respond to a "
                              f"TCP probe on port {device.mgmt_port}", now)
        return None
    return ingest_metrics(device, {"latency_ms": latency, "packet_loss": 0}, now)
