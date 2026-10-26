"""Report generation: availability, performance and alert summaries."""
from sqlalchemy import func

from . import db
from .models import Alert, Device, Metric

WINDOW_SECONDS = 300  # availability is measured in 5-minute windows

REPORT_TYPES = {
    "availability": "Device Availability Report",
    "performance": "Performance Summary Report",
    "alerts": "Fault & Alert Report",
}


def _devices(device_id):
    q = Device.query.order_by(Device.name)
    return q.filter_by(id=device_id).all() if device_id else q.all()


def availability_report(start, end, device_id=None):
    rows = []
    for d in _devices(device_id):
        window_start = max(start, d.created_at)
        expected = max(int((end - window_start).total_seconds() // WINDOW_SECONDS), 1)
        bucket = func.cast(func.strftime("%s", Metric.recorded_at), db.Integer) / WINDOW_SECONDS
        seen = (db.session.query(func.count(func.distinct(bucket)))
                .filter(Metric.device_id == d.id,
                        Metric.recorded_at >= window_start,
                        Metric.recorded_at < end).scalar() or 0)
        outages = (Alert.query.filter_by(device_id=d.id, alert_type="offline")
                   .filter(Alert.created_at >= start, Alert.created_at < end).count())
        rows.append({
            "device": d.name, "ip": d.ip_address, "type": d.device_type,
            "availability": round(min(seen / expected, 1) * 100, 2),
            "outages": outages, "status": d.status,
        })
    return rows


def performance_report(start, end, device_id=None):
    rows = []
    for d in _devices(device_id):
        r = (db.session.query(
            func.avg(Metric.cpu_usage), func.max(Metric.cpu_usage),
            func.avg(Metric.memory_usage), func.max(Metric.memory_usage),
            func.avg(Metric.latency_ms), func.max(Metric.latency_ms),
            func.avg(Metric.bandwidth_usage), func.max(Metric.bandwidth_usage),
            func.count(Metric.id))
            .filter(Metric.device_id == d.id, Metric.recorded_at >= start,
                    Metric.recorded_at < end).one())
        fmt = lambda v: round(v, 1) if v is not None else None  # noqa: E731
        rows.append({
            "device": d.name, "ip": d.ip_address,
            "cpu_avg": fmt(r[0]), "cpu_max": fmt(r[1]),
            "mem_avg": fmt(r[2]), "mem_max": fmt(r[3]),
            "lat_avg": fmt(r[4]), "lat_max": fmt(r[5]),
            "bw_avg": fmt(r[6]), "bw_max": fmt(r[7]), "samples": r[8],
        })
    return rows


def alert_report(start, end, device_id=None):
    q = Alert.query.filter(Alert.created_at >= start, Alert.created_at < end)
    if device_id:
        q = q.filter_by(device_id=device_id)
    alerts = q.order_by(Alert.created_at.desc()).all()
    by_sev = {"critical": 0, "warning": 0, "info": 0}
    by_type = {}
    resolve_minutes = []
    for a in alerts:
        by_sev[a.severity] = by_sev.get(a.severity, 0) + 1
        by_type[a.alert_type] = by_type.get(a.alert_type, 0) + 1
        if a.resolved_at:
            resolve_minutes.append((a.resolved_at - a.created_at).total_seconds() / 60)
    mttr = round(sum(resolve_minutes) / len(resolve_minutes), 1) if resolve_minutes else None
    return {"alerts": alerts, "by_severity": by_sev, "by_type": by_type,
            "total": len(alerts), "mttr": mttr,
            "open": sum(1 for a in alerts if a.status != "resolved")}
