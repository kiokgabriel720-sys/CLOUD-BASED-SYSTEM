"""REST API: agent metric submission (API-key auth) and chart data (session auth)."""
import hmac
from datetime import timedelta

from flask import Blueprint, abort, jsonify, request
from flask_login import login_required

from . import db
from .models import Alert, Device, Metric, log_event, utcnow
from .monitoring import MetricValidationError, ingest_metrics

api_bp = Blueprint("api", __name__)


def _device_from_key():
    key = request.headers.get("X-API-Key", "")
    if not key:
        return None
    for device in Device.query.filter_by(monitored=True).all():
        if hmac.compare_digest(device.api_key, key):  # constant-time comparison
            return device
    return None


@api_bp.route("/metrics", methods=["POST"])
def submit_metrics():
    """Endpoint called by monitoring agents every collection interval."""
    device = _device_from_key()
    if device is None:
        log_event("API_AUTH_FAILED", "invalid or missing X-API-Key",
                  ip=request.remote_addr)
        db.session.commit()
        return jsonify(error="invalid API key"), 401
    try:
        metric = ingest_metrics(device, request.get_json(silent=True))
    except MetricValidationError as exc:
        db.session.rollback()
        return jsonify(error=str(exc)), 422
    db.session.commit()
    return jsonify(status="accepted", device=device.name, device_status=device.status,
                   metric_id=metric.id), 201


@api_bp.route("/devices/<int:device_id>/metrics")
@login_required
def device_metrics(device_id):
    device = db.session.get(Device, device_id) or abort(404)
    hours = min(request.args.get("hours", 24, type=int), 24 * 31)
    since = utcnow() - timedelta(hours=hours)
    rows = (device.metrics.filter(Metric.recorded_at >= since)
            .order_by(Metric.recorded_at).all())
    return jsonify(device=device.name, points=[{
        "t": m.recorded_at.isoformat() + "Z", "cpu": m.cpu_usage, "memory": m.memory_usage,
        "latency": m.latency_ms, "bandwidth": m.bandwidth_usage,
        "mbps": m.bandwidth_mbps, "loss": m.packet_loss} for m in rows])


@api_bp.route("/summary")
@login_required
def summary():
    """Aggregated network-wide trend for the dashboard chart (hourly averages)."""
    hours = min(request.args.get("hours", 24, type=int), 168)
    since = utcnow() - timedelta(hours=hours)
    bucket = db.func.strftime("%Y-%m-%dT%H:00:00Z", Metric.recorded_at)
    rows = (db.session.query(bucket, db.func.avg(Metric.cpu_usage),
                             db.func.avg(Metric.memory_usage),
                             db.func.avg(Metric.latency_ms),
                             db.func.avg(Metric.bandwidth_usage))
            .filter(Metric.recorded_at >= since).group_by(bucket).order_by(bucket).all())
    sev = (db.session.query(Alert.severity, db.func.count(Alert.id))
           .filter(Alert.status != "resolved").group_by(Alert.severity).all())
    return jsonify(trend=[{"t": r[0], "cpu": _r(r[1]), "memory": _r(r[2]),
                           "latency": _r(r[3]), "bandwidth": _r(r[4])} for r in rows],
                   alerts_by_severity=dict(sev))


def _r(v):
    return round(v, 1) if v is not None else None
