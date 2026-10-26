"""Automated test suite for CloudNMS (unit, integration and system-level checks).

Test IDs match the test-case table in Chapter Three of the project documentation.
"""
from datetime import timedelta

from app import db
from app.models import Alert, AuditLog, Device, Metric, User, utcnow
from app.monitoring import check_heartbeats, ingest_metrics, validate_payload, \
    MetricValidationError
from conftest import login

API_KEY = {"X-API-Key": "k" * 48}


# ---------------------------------------------------------------- authentication
def test_tc01_valid_login_reaches_dashboard(client):
    r = login(client, "admin")
    assert r.status_code == 200
    assert b"Network overview" in r.data
    assert AuditLog.query.filter_by(action="LOGIN_SUCCESS").count() == 1


def test_tc02_invalid_login_denied_and_logged(client):
    r = client.post("/login", data={"username": "admin", "password": "wrong"})
    assert r.status_code == 401
    assert b"Invalid username or password" in r.data
    assert AuditLog.query.filter_by(action="LOGIN_FAILED").count() == 1


def test_tc02b_account_locks_after_five_failures(client):
    for _ in range(5):
        client.post("/login", data={"username": "tech", "password": "bad"})
    r = client.post("/login", data={"username": "tech", "password": "Passw0rd!"})
    assert r.status_code == 403
    assert User.query.filter_by(username="tech").first().locked_until is not None


def test_tc12_logout_protects_pages(admin):
    admin.get("/logout")
    r = admin.get("/devices")
    assert r.status_code == 302 and "/login" in r.headers["Location"]


def test_open_redirect_blocked(client):
    r = client.post("/login?next=https://evil.example.com",
                    data={"username": "admin", "password": "Passw0rd!"})
    assert r.headers["Location"] == "/"


# ---------------------------------------------------------------- users & RBAC
def test_tc03_admin_creates_user(admin):
    r = admin.post("/users/new", data={"full_name": "New Tech", "username": "newtech",
                                       "email": "nt@test.local", "role": "technician",
                                       "password": "Secure123"}, follow_redirects=True)
    assert b"saved" in r.data
    assert User.query.filter_by(username="newtech").first().role == "technician"


def test_weak_password_rejected(admin):
    r = admin.post("/users/new", data={"full_name": "X", "username": "xuser",
                                       "email": "x@test.local", "role": "technician",
                                       "password": "short"})
    assert r.status_code == 400
    assert User.query.filter_by(username="xuser").first() is None


def test_tc10_technician_cannot_manage_users_or_devices(tech):
    assert tech.get("/users").status_code == 403
    assert tech.get("/devices/new").status_code == 403
    assert AuditLog.query.filter_by(action="AUTHZ_DENIED").count() == 2


def test_network_admin_cannot_delete_device(netadmin, device):
    assert netadmin.post(f"/devices/{device.id}/delete").status_code == 403
    assert db.session.get(Device, device.id) is not None


# ---------------------------------------------------------------- devices
def test_tc04_add_valid_device(netadmin):
    r = netadmin.post("/devices/new", data={"name": "Lab Switch", "ip_address": "10.0.1.5",
                                            "device_type": "Switch", "mgmt_port": "22"},
                      follow_redirects=True)
    assert b"registered" in r.data
    d = Device.query.filter_by(ip_address="10.0.1.5").first()
    assert d and len(d.api_key) == 48


def test_tc11_invalid_device_rejected(netadmin):
    r = netadmin.post("/devices/new", data={"name": "Bad", "ip_address": "999.1.1.1",
                                            "device_type": "Switch"})
    assert r.status_code == 400 and b"valid IPv4 or IPv6" in r.data
    r = netadmin.post("/devices/new", data={"name": "Dup", "ip_address": "10.0.0.1",
                                            "device_type": "Router"})
    assert b"already registered" in r.data
    assert Device.query.count() == 1


def test_tc05_search_device(tech):
    r = tech.get("/devices?q=10.0.0")
    assert b"Core Router" in r.data
    r = tech.get("/devices?q=nonexistent")
    assert b"No devices match" in r.data


# ---------------------------------------------------------------- monitoring API
def test_tc06_agent_submits_metrics(client, device):
    r = client.post("/api/v1/metrics", headers=API_KEY,
                    json={"cpu_usage": 35, "memory_usage": 50, "latency_ms": 12,
                          "bandwidth_usage": 40, "packet_loss": 0})
    assert r.status_code == 201 and r.json["device_status"] == "online"
    assert Metric.query.count() == 1
    assert db.session.get(Device, device.id).last_seen is not None


def test_api_rejects_bad_key(client):
    r = client.post("/api/v1/metrics", headers={"X-API-Key": "bogus"}, json={"cpu_usage": 1})
    assert r.status_code == 401
    assert AuditLog.query.filter_by(action="API_AUTH_FAILED").count() == 1


def test_api_validates_payload(client):
    r = client.post("/api/v1/metrics", headers=API_KEY, json={"cpu_usage": 180})
    assert r.status_code == 422
    assert Metric.query.count() == 0


def test_validate_payload_unit():
    assert validate_payload({"cpu_usage": "42.456"}) == {"cpu_usage": 42.46}
    for bad in ({}, {"cpu_usage": "abc"}, {"latency_ms": -1}, []):
        try:
            validate_payload(bad)
            assert False, f"{bad} accepted"
        except MetricValidationError:
            pass


# ---------------------------------------------------------------- fault detection
def test_tc07_silent_device_goes_offline(app, device):
    device.last_seen = utcnow() - timedelta(minutes=10)
    db.session.commit()
    offline = check_heartbeats()
    assert device in offline and device.status == "offline"
    a = Alert.query.filter_by(alert_type="offline").one()
    assert a.severity == "critical" and a.status == "open"
    # recovery resolves the alert automatically
    ingest_metrics(device, {"cpu_usage": 20})
    assert a.status == "resolved" and device.status == "online"


def test_tc08_critical_metric_raises_alert_immediately(app, device):
    ingest_metrics(device, {"cpu_usage": 95})
    a = Alert.query.filter_by(alert_type="cpu").one()
    assert a.severity == "critical" and device.status == "warning"


def test_warning_needs_two_consecutive_samples(app, device):
    t = utcnow()
    ingest_metrics(device, {"latency_ms": 120}, now=t)
    assert Alert.query.count() == 0          # single spike ignored
    ingest_metrics(device, {"latency_ms": 20}, now=t + timedelta(minutes=1))
    ingest_metrics(device, {"latency_ms": 130}, now=t + timedelta(minutes=2))
    ingest_metrics(device, {"latency_ms": 140}, now=t + timedelta(minutes=3))
    assert Alert.query.filter_by(alert_type="latency", severity="warning").count() == 1


def test_no_duplicate_alerts_and_hysteresis(app, device):
    t = utcnow()
    for i, cpu in enumerate([95, 96, 97, 78, 72]):
        ingest_metrics(device, {"cpu_usage": cpu}, now=t + timedelta(minutes=i))
    alerts = Alert.query.filter_by(alert_type="cpu").all()
    assert len(alerts) == 1                   # one alert per fault, not per sample
    assert alerts[0].status == "open"         # 78 and 72 are inside the hysteresis band
    ingest_metrics(device, {"cpu_usage": 40}, now=t + timedelta(minutes=6))
    assert alerts[0].status == "resolved"


def test_alert_acknowledge_and_resolve(netadmin, device):
    ingest_metrics(device, {"cpu_usage": 99})
    db.session.commit()
    a = Alert.query.one()
    netadmin.post(f"/alerts/{a.id}/acknowledge")
    assert db.session.get(Alert, a.id).status == "acknowledged"
    netadmin.post(f"/alerts/{a.id}/resolve")
    assert db.session.get(Alert, a.id).status == "resolved"


def test_technician_cannot_resolve_alert(tech, device):
    ingest_metrics(device, {"cpu_usage": 99})
    db.session.commit()
    a = Alert.query.one()
    assert tech.post(f"/alerts/{a.id}/resolve").status_code == 403


# ---------------------------------------------------------------- reports
def test_tc09_generate_and_export_reports(admin, device):
    t = utcnow()
    for i in range(12):
        ingest_metrics(device, {"cpu_usage": 30 + i, "latency_ms": 10},
                       now=t - timedelta(minutes=5 * i))
    db.session.commit()
    for rtype in ("availability", "performance", "alerts"):
        r = admin.get(f"/reports?type={rtype}")
        assert r.status_code == 200 and b"Core Router" in r.data
    r = admin.get("/reports/export?type=performance")
    assert r.mimetype == "text/csv" and b"Core Router" in r.data
    assert b"41.0" in r.data  # max CPU value


def test_report_rejects_bad_period(admin):
    r = admin.get("/reports?type=alerts&start=2026-09-10&end=2026-09-01")
    assert r.status_code == 400


def test_dashboard_and_summary_api(admin, device):
    ingest_metrics(device, {"cpu_usage": 30, "memory_usage": 40})
    db.session.commit()
    assert admin.get("/").status_code == 200
    data = admin.get("/api/v1/summary").json
    assert data["trend"][0]["cpu"] == 30.0


def test_performance_report_device_without_data(admin, device):
    """Regression: devices with no samples showed 'None%' in the performance report."""
    r = admin.get("/reports?type=performance")
    assert b"None%" not in r.data and b"No data received" in r.data


def test_default_admin_created_on_first_run_and_hint_hidden_after_change():
    from app import create_app
    import config

    class FirstRun(config.TestConfig):
        CREATE_DEFAULT_ADMIN = True

    app = create_app(FirstRun)
    c = app.test_client()
    with app.app_context():
        assert User.query.filter_by(username="admin", role="admin").count() == 1
    page = c.get("/login").data
    assert b"First-time login" in page and b"Admin@2026" in page
    r = c.post("/login", data={"username": "admin", "password": "Admin@2026"}, follow_redirects=True)
    assert b"Network overview" in r.data
    c.post("/profile", data={"current_password": "Admin@2026", "new_password": "NewPass2026",
                             "confirm_password": "NewPass2026"})
    c.get("/logout")
    assert b"First-time login" not in c.get("/login").data
    with app.app_context():
        db.drop_all()
