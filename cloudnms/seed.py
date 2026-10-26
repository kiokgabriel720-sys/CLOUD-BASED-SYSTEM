"""Load demonstration data: users, a campus lab network of 8 devices and N days of metrics.

The historical metrics are passed through the real monitoring engine, so every alert
in the demo database was raised by the same threshold/heartbeat logic used in production.
"""
import math
import random
from datetime import timedelta

from app import db
from app.models import Alert, AuditLog, Device, Metric, Report, User, get_thresholds, utcnow
from app.monitoring import check_heartbeats, ingest_metrics

USERS = [
    ("System Administrator", "admin", "admin@cloudnms.local", "admin", "Admin@2026"),
    ("Gabriel Kiok Kunterei", "gkunterei", "gkunterei@cloudnms.local", "admin", "Admin@2026"),
    ("Jane Wanjiku", "jwanjiku", "jwanjiku@cloudnms.local", "network_admin", "Netadmin@2026"),
    ("Brian Omondi", "bomondi", "bomondi@cloudnms.local", "technician", "Tech@2026"),
]
DEVICES = [  # name, ip, type, vendor, location, port, profile
    ("Core Router", "10.10.0.1", "Router", "MikroTik CCR2004", "Server Room, Block A", 22, "router"),
    ("Edge Firewall", "10.10.0.2", "Firewall", "pfSense Netgate 4100", "Server Room, Block A", 443, "firewall"),
    ("Distribution Switch", "10.10.1.1", "Switch", "Cisco Catalyst 9300", "Server Room, Block A", 22, "switch"),
    ("Access Switch - Lab 1", "10.10.1.11", "Switch", "Cisco CBS350-24", "Computer Lab 1", 22, "switch"),
    ("Access Switch - Library", "10.10.1.12", "Switch", "TP-Link TL-SG3428", "Library", 22, "switch"),
    ("Web Server", "10.10.2.10", "Server", "Ubuntu 24.04 VM", "Cloud (VPS)", 443, "server"),
    ("Database Server", "10.10.2.20", "Server", "Ubuntu 24.04 VM", "Server Room, Block A", 22, "server"),
    ("Wi-Fi AP - Admin Block", "10.10.3.5", "Access Point", "Ubiquiti U6-Pro", "Administration Block", 22, "ap"),
]
BASE = {
    "router": (35, 48, 12, 45), "firewall": (40, 55, 6, 40), "switch": (18, 35, 3, 38),
    "server": (28, 62, 4, 22), "ap": (22, 40, 28, 30),
}


def _sample(profile, t):
    cpu, mem, lat, bw = BASE[profile]
    hour = (t.hour + 3) % 24  # local (EAT) business-hours load curve
    load = 0.55 + 0.45 * max(0, math.sin((hour - 6) / 14 * math.pi)) if 6 <= hour <= 20 else 0.5
    g = random.gauss
    return {
        "cpu_usage": round(min(100, max(1, cpu * load * 1.3 + g(0, 4))), 1),
        "memory_usage": round(min(100, max(5, mem + g(0, 2.5))), 1),
        "latency_ms": round(max(0.5, lat * (0.8 + load * 0.5) + g(0, lat * 0.15)), 1),
        "bandwidth_usage": round(min(100, max(1, bw * load * 1.35 + g(0, 5))), 1),
        "bandwidth_mbps": None,
        "packet_loss": round(max(0, g(0.1, 0.15)), 2),
    }


def _reset_database():
    """Start from an empty database file; explain clearly if it is in use."""
    import os
    import sys
    url = db.engine.url
    db.session.remove()
    db.engine.dispose()
    path = url.database
    if url.drivername.startswith("sqlite") and path and path != ":memory:" and os.path.exists(path):
        try:
            os.remove(path)
        except PermissionError:
            sys.exit("\nThe database is in use by another program - CloudNMS is probably still "
                     "running.\nStop it (Ctrl+C in its terminal, or close that terminal) and run "
                     "seed.py again.\n")
    db.create_all()


def seed(days=7):
    random.seed(42)
    _reset_database()
    from app.models import ensure_default_settings
    ensure_default_settings()
    now = utcnow().replace(second=0, microsecond=0)
    start = now - timedelta(days=days)

    users = []
    for name, uname, email, role, pw in USERS:
        u = User(full_name=name, username=uname, email=email, role=role,
                 created_at=start - timedelta(days=3))
        u.set_password(pw)
        db.session.add(u)
        users.append(u)
    devices = []
    for name, ip, dtype, vendor, loc, port, profile in DEVICES:
        d = Device(name=name, ip_address=ip, device_type=dtype, vendor=vendor, location=loc,
                   mgmt_port=port, created_at=start - timedelta(minutes=5))
        d.profile = profile
        db.session.add(d)
        devices.append(d)
    db.session.commit()

    thresholds = get_thresholds()
    by_name = {d.name: d for d in devices}
    step = timedelta(minutes=5)
    t = start
    while t <= now:
        age = now - t
        for d in devices:
            # --- scripted incidents ---------------------------------------------
            if d.name == "Core Router" and timedelta(days=4, minutes=40) >= age > timedelta(days=4):
                continue  # 40-minute outage four days ago
            if d.name == "Access Switch - Library" and age < timedelta(hours=2):
                continue  # currently offline (power failure in library)
            s = _sample(d.profile, t)
            if d.name == "Edge Firewall" and timedelta(days=2, hours=1) >= age > timedelta(days=2):
                s["cpu_usage"] = round(random.uniform(88, 96), 1)  # traffic surge
            if d.name == "Distribution Switch" and timedelta(days=1, hours=2) >= age > timedelta(days=1):
                s["bandwidth_usage"] = round(random.uniform(82, 91), 1)  # backup window
            if d.name == "Wi-Fi AP - Admin Block" and ((t.hour + 3) % 24) in (17, 18) and random.random() < .5:
                s["latency_ms"] = round(random.uniform(105, 190), 1)  # evening congestion
            if d.name == "Web Server" and age < timedelta(hours=3):
                s["memory_usage"] = round(84 + (3 - age.total_seconds() / 3600) * 1.5
                                          + random.uniform(-0.5, 0.5), 1)  # memory leak
            s["bandwidth_mbps"] = round(s["bandwidth_usage"] * 10, 1)
            ingest_metrics(d, s, now=t, thresholds=thresholds)
        check_heartbeats(now=t, timeout=180)
        t += step
        if t.minute == 0 and t.hour == 0:
            db.session.commit()
    check_heartbeats(now=now + timedelta(minutes=1), timeout=180)
    db.session.commit()

    # Operator activity: acknowledge some alerts, record a few reports
    jane = users[2]
    for a in Alert.query.filter(Alert.status != "resolved").all():
        if a.alert_type == "memory":
            a.status, a.acknowledged_by, a.acknowledged_at = "acknowledged", jane.id, now - timedelta(minutes=40)
    for i, rtype in enumerate(["availability", "performance", "alerts"]):
        db.session.add(Report(user_id=jane.id, report_type=rtype, period_start=now - timedelta(days=7),
                              period_end=now, created_at=now - timedelta(days=1, hours=i)))
    db.session.add(AuditLog(user_id=users[1].id, action="USER_CREATED", details="jwanjiku role=network_admin",
                            ip_address="41.90.64.12", timestamp=start - timedelta(days=2)))
    db.session.add(AuditLog(user_id=users[1].id, action="USER_CREATED", details="bomondi role=technician",
                            ip_address="41.90.64.12", timestamp=start - timedelta(days=2)))
    for d in devices:
        db.session.add(AuditLog(user_id=users[1].id, action="DEVICE_ADDED", details=f"{d.name} ({d.ip_address})",
                                ip_address="41.90.64.12", timestamp=d.created_at))
    db.session.commit()
    print(f"Seeded {Metric.query.count()} metrics, {Alert.query.count()} alerts for {len(devices)} devices.")


if __name__ == "__main__":
    import sys
    from app import create_app
    app = create_app()
    with app.app_context():
        seed(int(sys.argv[1]) if len(sys.argv) > 1 else 7)
