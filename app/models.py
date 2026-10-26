"""Database models (SQLAlchemy ORM) for CloudNMS."""
import secrets
from datetime import datetime, timezone

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from . import db, login_manager

ROLES = {
    "admin": "System Administrator",
    "network_admin": "Network Administrator",
    "technician": "IT Technician",
}
DEVICE_TYPES = ["Router", "Switch", "Firewall", "Server", "Access Point", "Other"]


def utcnow():
    """Naive UTC timestamp (SQLite stores naive datetimes)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(UserMixin, db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(120), nullable=False)
    username = db.Column(db.String(50), unique=True, nullable=False, index=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="technician")
    active = db.Column(db.Boolean, nullable=False, default=True)
    failed_logins = db.Column(db.Integer, nullable=False, default=0)
    locked_until = db.Column(db.DateTime)
    last_login = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    reports = db.relationship("Report", backref="user", lazy=True)
    audit_logs = db.relationship("AuditLog", backref="user", lazy=True)

    @property
    def is_active(self):  # used by Flask-Login
        return self.active

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def has_role(self, *roles):
        return self.role in roles

    @property
    def role_label(self):
        return ROLES.get(self.role, self.role)


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


class Device(db.Model):
    __tablename__ = "network_devices"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    ip_address = db.Column(db.String(45), unique=True, nullable=False, index=True)
    device_type = db.Column(db.String(30), nullable=False)
    vendor = db.Column(db.String(60))
    location = db.Column(db.String(120))
    mgmt_port = db.Column(db.Integer, default=22)
    api_key = db.Column(db.String(64), unique=True, nullable=False,
                        default=lambda: secrets.token_hex(24))
    status = db.Column(db.String(20), nullable=False, default="unknown", index=True)
    monitored = db.Column(db.Boolean, nullable=False, default=True)
    last_seen = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    metrics = db.relationship("Metric", backref="device", lazy="dynamic",
                              cascade="all, delete-orphan")
    alerts = db.relationship("Alert", backref="device", lazy="dynamic",
                             cascade="all, delete-orphan")

    def latest_metric(self):
        return self.metrics.order_by(Metric.recorded_at.desc()).first()

    def regenerate_key(self):
        self.api_key = secrets.token_hex(24)


class Metric(db.Model):
    __tablename__ = "performance_metrics"
    id = db.Column(db.Integer, primary_key=True)
    device_id = db.Column(db.Integer, db.ForeignKey("network_devices.id"),
                          nullable=False, index=True)
    cpu_usage = db.Column(db.Float)          # %
    memory_usage = db.Column(db.Float)       # %
    bandwidth_usage = db.Column(db.Float)    # % of link capacity
    bandwidth_mbps = db.Column(db.Float)     # throughput in Mbps
    latency_ms = db.Column(db.Float)         # round-trip time
    packet_loss = db.Column(db.Float)        # %
    recorded_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)


class Alert(db.Model):
    __tablename__ = "alerts"
    id = db.Column(db.Integer, primary_key=True)
    device_id = db.Column(db.Integer, db.ForeignKey("network_devices.id"),
                          nullable=False, index=True)
    alert_type = db.Column(db.String(30), nullable=False)   # offline, cpu, memory ...
    severity = db.Column(db.String(10), nullable=False)     # critical, warning, info
    message = db.Column(db.String(255), nullable=False)
    status = db.Column(db.String(15), nullable=False, default="open", index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)
    acknowledged_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    acknowledged_at = db.Column(db.DateTime)
    resolved_at = db.Column(db.DateTime)

    acknowledger = db.relationship("User", foreign_keys=[acknowledged_by])


class Report(db.Model):
    __tablename__ = "reports"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    report_type = db.Column(db.String(30), nullable=False)
    device_id = db.Column(db.Integer, db.ForeignKey("network_devices.id"))
    period_start = db.Column(db.DateTime, nullable=False)
    period_end = db.Column(db.DateTime, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)


class AuditLog(db.Model):
    __tablename__ = "audit_logs"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    action = db.Column(db.String(60), nullable=False, index=True)
    details = db.Column(db.String(255))
    ip_address = db.Column(db.String(45))
    timestamp = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)


class Setting(db.Model):
    __tablename__ = "settings"
    key = db.Column(db.String(50), primary_key=True)
    value = db.Column(db.Float, nullable=False)
    description = db.Column(db.String(120))


DEFAULT_SETTINGS = {
    "cpu_warning": (80, "CPU utilisation warning threshold (%)"),
    "cpu_critical": (90, "CPU utilisation critical threshold (%)"),
    "memory_warning": (80, "Memory utilisation warning threshold (%)"),
    "memory_critical": (92, "Memory utilisation critical threshold (%)"),
    "latency_warning": (100, "Latency warning threshold (ms)"),
    "latency_critical": (250, "Latency critical threshold (ms)"),
    "bandwidth_warning": (80, "Link utilisation warning threshold (%)"),
    "bandwidth_critical": (95, "Link utilisation critical threshold (%)"),
    "packet_loss_warning": (2, "Packet loss warning threshold (%)"),
    "packet_loss_critical": (10, "Packet loss critical threshold (%)"),
}


def ensure_default_settings():
    for key, (value, desc) in DEFAULT_SETTINGS.items():
        if db.session.get(Setting, key) is None:
            db.session.add(Setting(key=key, value=value, description=desc))
    db.session.commit()


def get_thresholds():
    return {s.key: s.value for s in Setting.query.all()}


def log_event(action, details="", user=None, ip=None):
    """Write an audit-log record. Passwords and API keys are never logged."""
    from flask import has_request_context, request
    from flask_login import current_user
    if user is None and has_request_context() and current_user.is_authenticated:
        user = current_user
    if ip is None and has_request_context():
        ip = request.headers.get("X-Forwarded-For", request.remote_addr)
    db.session.add(AuditLog(user_id=user.id if user else None, action=action,
                            details=details[:255], ip_address=ip))


def ensure_default_admin(app):
    """Create the first System Administrator if the users table is empty."""
    if not app.config.get("CREATE_DEFAULT_ADMIN") or User.query.first() is not None:
        return None
    user = User(full_name="System Administrator", username=app.config["DEFAULT_ADMIN_USERNAME"],
                email="admin@cloudnms.local", role="admin")
    user.set_password(app.config["DEFAULT_ADMIN_PASSWORD"])
    db.session.add(user)
    db.session.commit()
    print(f" * Default administrator created -> username: {user.username}  "
          f"password: {app.config['DEFAULT_ADMIN_PASSWORD']}  (change it after logging in)")
    return user


def default_password_in_use(app):
    """True while the default administrator still uses the default password."""
    user = User.query.filter_by(username=app.config.get("DEFAULT_ADMIN_USERNAME")).first()
    return bool(user and user.check_password(app.config.get("DEFAULT_ADMIN_PASSWORD", "")))
