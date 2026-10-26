"""Web console routes: dashboard, devices, alerts, reports, users, audit log and settings."""
import csv
import io
import ipaddress
from datetime import datetime, timedelta

from flask import (Blueprint, Response, abort, flash, redirect, render_template,
                   request, url_for)
from flask_login import current_user, login_required
from sqlalchemy import or_

from . import db
from .decorators import role_required
from .models import (DEVICE_TYPES, ROLES, Alert, AuditLog, Device, Metric, Report,
                     Setting, User, log_event, utcnow)
from .monitoring import check_heartbeats, poll_device
from .reports import REPORT_TYPES, alert_report, availability_report, performance_report

main_bp = Blueprint("main", __name__)
MANAGERS = ("admin", "network_admin")


# --------------------------------------------------------------------------- dashboard
@main_bp.route("/")
@login_required
def dashboard():
    newly_offline = check_heartbeats()
    if newly_offline:
        db.session.commit()
    devices = Device.query.filter_by(monitored=True).all()
    counts = {s: sum(1 for d in devices if d.status == s)
              for s in ("online", "warning", "offline", "unknown")}
    since = utcnow() - timedelta(hours=24)
    recent_alerts = (Alert.query.filter(Alert.status != "resolved")
                     .order_by(Alert.created_at.desc()).limit(6).all())
    alerts_24h = Alert.query.filter(Alert.created_at >= since).count()
    critical_open = Alert.query.filter(Alert.status != "resolved",
                                       Alert.severity == "critical").count()
    problem_devices = sorted([d for d in devices if d.status in ("offline", "warning")],
                             key=lambda d: d.status != "offline")
    avail = round(100 * (counts["online"] + counts["warning"]) / len(devices), 1) if devices else 0
    return render_template("dashboard.html", counts=counts, total=len(devices),
                           recent_alerts=recent_alerts, alerts_24h=alerts_24h,
                           critical_open=critical_open, devices=devices,
                           problem_devices=problem_devices, availability=avail)


# --------------------------------------------------------------------------- devices
def _device_form_errors(form, device=None):
    errors = []
    name = form.get("name", "").strip()
    ip = form.get("ip_address", "").strip()
    if not name or len(name) > 100:
        errors.append("Device name is required (max 100 characters).")
    try:
        ipaddress.ip_address(ip)
    except ValueError:
        errors.append("Enter a valid IPv4 or IPv6 address.")
    else:
        clash = Device.query.filter_by(ip_address=ip).first()
        if clash and (device is None or clash.id != device.id):
            errors.append(f"IP address {ip} is already registered to {clash.name}.")
    if form.get("device_type") not in DEVICE_TYPES:
        errors.append("Select a valid device type.")
    try:
        port = int(form.get("mgmt_port") or 22)
        if not 1 <= port <= 65535:
            raise ValueError
    except ValueError:
        errors.append("Management port must be between 1 and 65535.")
    return errors


def _apply_device_form(device, form):
    device.name = form["name"].strip()
    device.ip_address = form["ip_address"].strip()
    device.device_type = form["device_type"]
    device.vendor = form.get("vendor", "").strip() or None
    device.location = form.get("location", "").strip() or None
    device.mgmt_port = int(form.get("mgmt_port") or 22)


@main_bp.route("/devices")
@login_required
def devices():
    q = request.args.get("q", "").strip()
    status = request.args.get("status", "")
    dtype = request.args.get("type", "")
    query = Device.query
    if q:
        like = f"%{q}%"
        query = query.filter(or_(Device.name.ilike(like), Device.ip_address.ilike(like),
                                 Device.location.ilike(like), Device.vendor.ilike(like)))
    if status:
        query = query.filter_by(status=status)
    if dtype:
        query = query.filter_by(device_type=dtype)
    return render_template("devices.html", devices=query.order_by(Device.name).all(),
                           q=q, status=status, dtype=dtype, device_types=DEVICE_TYPES)


@main_bp.route("/devices/new", methods=["GET", "POST"])
@login_required
@role_required(*MANAGERS)
def add_device():
    if request.method == "POST":
        errors = _device_form_errors(request.form)
        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("device_form.html", device=None, form=request.form,
                                   device_types=DEVICE_TYPES), 400
        device = Device()
        _apply_device_form(device, request.form)
        db.session.add(device)
        db.session.flush()
        log_event("DEVICE_ADDED", f"{device.name} ({device.ip_address})")
        db.session.commit()
        flash(f"Device '{device.name}' registered. Configure its agent with the API key "
              "shown below.", "success")
        return redirect(url_for("main.device_detail", device_id=device.id))
    return render_template("device_form.html", device=None, form={},
                           device_types=DEVICE_TYPES)


@main_bp.route("/devices/<int:device_id>")
@login_required
def device_detail(device_id):
    device = db.session.get(Device, device_id) or abort(404)
    latest = device.latest_metric()
    alerts = device.alerts.order_by(Alert.created_at.desc()).limit(10).all()
    return render_template("device_detail.html", device=device, latest=latest,
                           alerts=alerts)


@main_bp.route("/devices/<int:device_id>/edit", methods=["GET", "POST"])
@login_required
@role_required(*MANAGERS)
def edit_device(device_id):
    device = db.session.get(Device, device_id) or abort(404)
    if request.method == "POST":
        errors = _device_form_errors(request.form, device)
        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("device_form.html", device=device, form=request.form,
                                   device_types=DEVICE_TYPES), 400
        _apply_device_form(device, request.form)
        log_event("DEVICE_UPDATED", f"{device.name} ({device.ip_address})")
        db.session.commit()
        flash("Device details updated.", "success")
        return redirect(url_for("main.device_detail", device_id=device.id))
    return render_template("device_form.html", device=device, form=device.__dict__,
                           device_types=DEVICE_TYPES)


@main_bp.route("/devices/<int:device_id>/toggle", methods=["POST"])
@login_required
@role_required(*MANAGERS)
def toggle_device(device_id):
    device = db.session.get(Device, device_id) or abort(404)
    device.monitored = not device.monitored
    if not device.monitored:
        device.status = "unknown"
    log_event("DEVICE_MONITORING", f"{device.name} monitored={device.monitored}")
    db.session.commit()
    flash(f"Monitoring {'enabled' if device.monitored else 'paused'} for {device.name}.",
          "success")
    return redirect(url_for("main.device_detail", device_id=device.id))


@main_bp.route("/devices/<int:device_id>/poll", methods=["POST"])
@login_required
@role_required(*MANAGERS)
def poll(device_id):
    device = db.session.get(Device, device_id) or abort(404)
    metric = poll_device(device)
    log_event("DEVICE_POLLED", device.name)
    db.session.commit()
    if metric:
        flash(f"{device.name} responded in {metric.latency_ms} ms.", "success")
    else:
        flash(f"{device.name} did not respond on port {device.mgmt_port}.", "danger")
    return redirect(url_for("main.device_detail", device_id=device.id))


@main_bp.route("/devices/<int:device_id>/regenerate-key", methods=["POST"])
@login_required
@role_required("admin")
def regenerate_key(device_id):
    device = db.session.get(Device, device_id) or abort(404)
    device.regenerate_key()
    log_event("API_KEY_ROTATED", device.name)
    db.session.commit()
    flash("A new agent API key was generated. Update the agent configuration.", "warning")
    return redirect(url_for("main.device_detail", device_id=device.id))


@main_bp.route("/devices/<int:device_id>/delete", methods=["POST"])
@login_required
@role_required("admin")
def delete_device(device_id):
    device = db.session.get(Device, device_id) or abort(404)
    name = device.name
    db.session.delete(device)
    log_event("DEVICE_DELETED", name)
    db.session.commit()
    flash(f"Device '{name}' and its monitoring history were deleted.", "success")
    return redirect(url_for("main.devices"))


# --------------------------------------------------------------------------- alerts
@main_bp.route("/alerts")
@login_required
def alerts():
    status = request.args.get("status", "active")
    severity = request.args.get("severity", "")
    query = Alert.query.join(Device)
    if status == "active":
        query = query.filter(Alert.status != "resolved")
    elif status in ("open", "acknowledged", "resolved"):
        query = query.filter(Alert.status == status)
    if severity:
        query = query.filter(Alert.severity == severity)
    items = query.order_by(Alert.created_at.desc()).limit(200).all()
    return render_template("alerts.html", alerts=items, status=status, severity=severity)


@main_bp.route("/alerts/<int:alert_id>/<action>", methods=["POST"])
@login_required
def alert_action(alert_id, action):
    alert = db.session.get(Alert, alert_id) or abort(404)
    if action == "acknowledge" and alert.status == "open":
        alert.status = "acknowledged"
        alert.acknowledged_by = current_user.id
        alert.acknowledged_at = utcnow()
    elif action == "resolve" and current_user.has_role(*MANAGERS):
        alert.status = "resolved"
        alert.resolved_at = utcnow()
    else:
        abort(403 if action == "resolve" else 400)
    log_event(f"ALERT_{action.upper()}", f"alert #{alert.id} {alert.device.name}")
    db.session.commit()
    flash(f"Alert #{alert.id} {action}d.", "success")
    return redirect(request.referrer or url_for("main.alerts"))


# --------------------------------------------------------------------------- reports
def _parse_period(form):
    today = utcnow().date()
    start = form.get("start") or (today - timedelta(days=6)).isoformat()
    end = form.get("end") or today.isoformat()
    start_dt = datetime.fromisoformat(start)
    end_dt = datetime.fromisoformat(end) + timedelta(days=1)
    if end_dt <= start_dt:
        raise ValueError("End date must be on or after the start date.")
    if (end_dt - start_dt).days > 92:
        raise ValueError("Reports are limited to 92 days.")
    return start, end, start_dt, end_dt


@main_bp.route("/reports")
@login_required
def reports():
    args = request.args
    rtype = args.get("type")
    device_id = args.get("device_id", type=int)
    devices = Device.query.order_by(Device.name).all()
    context = {"devices": devices, "report_types": REPORT_TYPES, "rtype": rtype,
               "device_id": device_id, "result": None}
    try:
        start, end, start_dt, end_dt = _parse_period(args)
    except ValueError as exc:
        flash(str(exc), "danger")
        return render_template("reports.html", start="", end="", **context), 400
    context.update(start=start, end=end)
    if rtype in REPORT_TYPES:
        builder = {"availability": availability_report, "performance": performance_report,
                   "alerts": alert_report}[rtype]
        context["result"] = builder(start_dt, end_dt, device_id)
        db.session.add(Report(user_id=current_user.id, report_type=rtype,
                              device_id=device_id, period_start=start_dt,
                              period_end=end_dt))
        log_event("REPORT_GENERATED", f"{rtype} {start}..{end}")
        db.session.commit()
    context["history"] = (Report.query.order_by(Report.created_at.desc()).limit(5).all())
    return render_template("reports.html", **context)


@main_bp.route("/reports/export")
@login_required
def export_report():
    rtype = request.args.get("type")
    device_id = request.args.get("device_id", type=int)
    try:
        start, end, start_dt, end_dt = _parse_period(request.args)
    except ValueError:
        abort(400)
    out = io.StringIO()
    writer = csv.writer(out)
    if rtype == "availability":
        rows = availability_report(start_dt, end_dt, device_id)
        writer.writerow(["Device", "IP address", "Type", "Availability %", "Outages",
                         "Current status"])
        for r in rows:
            writer.writerow([r["device"], r["ip"], r["type"], r["availability"],
                             r["outages"], r["status"]])
    elif rtype == "performance":
        rows = performance_report(start_dt, end_dt, device_id)
        writer.writerow(["Device", "IP", "CPU avg", "CPU max", "Mem avg", "Mem max",
                         "Latency avg (ms)", "Latency max (ms)", "Link avg %",
                         "Link max %", "Samples"])
        for r in rows:
            writer.writerow([r[k] for k in ("device", "ip", "cpu_avg", "cpu_max", "mem_avg",
                                            "mem_max", "lat_avg", "lat_max", "bw_avg",
                                            "bw_max", "samples")])
    elif rtype == "alerts":
        data = alert_report(start_dt, end_dt, device_id)
        writer.writerow(["Alert ID", "Device", "Type", "Severity", "Message", "Status",
                         "Raised (UTC)", "Resolved (UTC)"])
        for a in data["alerts"]:
            writer.writerow([a.id, a.device.name, a.alert_type, a.severity, a.message,
                             a.status, a.created_at.isoformat(timespec="minutes"),
                             a.resolved_at.isoformat(timespec="minutes")
                             if a.resolved_at else ""])
    else:
        abort(400)
    log_event("REPORT_EXPORTED", f"{rtype} {start}..{end}")
    db.session.commit()
    filename = f"cloudnms_{rtype}_{start}_to_{end}.csv"
    return Response(out.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={filename}"})


# --------------------------------------------------------------------------- users
@main_bp.route("/users")
@login_required
@role_required("admin")
def users():
    return render_template("users.html", users=User.query.order_by(User.full_name).all(),
                           roles=ROLES)


@main_bp.route("/users/new", methods=["GET", "POST"])
@main_bp.route("/users/<int:user_id>/edit", methods=["GET", "POST"])
@login_required
@role_required("admin")
def user_form(user_id=None):
    user = db.session.get(User, user_id) if user_id else None
    if user_id and user is None:
        abort(404)
    if request.method == "POST":
        f = request.form
        errors = []
        username = f.get("username", "").strip().lower()
        email = f.get("email", "").strip().lower()
        if not f.get("full_name", "").strip():
            errors.append("Full name is required.")
        if not username.isalnum() or len(username) < 3:
            errors.append("Username must be at least 3 letters/numbers.")
        if "@" not in email:
            errors.append("Enter a valid email address.")
        if f.get("role") not in ROLES:
            errors.append("Select a valid role.")
        other = User.query.filter(or_(User.username == username, User.email == email)).first()
        if other and (user is None or other.id != user.id):
            errors.append("Username or email already in use.")
        pw = f.get("password", "")
        if user is None or pw:
            if len(pw) < 8 or pw.isalpha() or pw.isdigit():
                errors.append("Password must be at least 8 characters and mix letters "
                              "and numbers.")
        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("user_form.html", user=user, form=f, roles=ROLES), 400
        creating = user is None
        user = user or User()
        user.full_name = f["full_name"].strip()
        user.username = username
        user.email = email
        user.role = f["role"]
        if pw:
            user.set_password(pw)
        if creating:
            db.session.add(user)
        log_event("USER_CREATED" if creating else "USER_UPDATED",
                  f"{username} role={user.role}")
        db.session.commit()
        flash(f"User '{username}' saved.", "success")
        return redirect(url_for("main.users"))
    form = {"full_name": user.full_name, "username": user.username, "email": user.email,
            "role": user.role} if user else {}
    return render_template("user_form.html", user=user, form=form, roles=ROLES)


@main_bp.route("/users/<int:user_id>/toggle", methods=["POST"])
@login_required
@role_required("admin")
def toggle_user(user_id):
    user = db.session.get(User, user_id) or abort(404)
    if user.id == current_user.id:
        flash("You cannot deactivate your own account.", "danger")
        return redirect(url_for("main.users"))
    user.active = not user.active
    log_event("USER_STATUS", f"{user.username} active={user.active}")
    db.session.commit()
    flash(f"{user.username} {'activated' if user.active else 'deactivated'}.", "success")
    return redirect(url_for("main.users"))


# --------------------------------------------------------------------------- audit & settings
@main_bp.route("/audit")
@login_required
@role_required("admin")
def audit():
    action = request.args.get("action", "")
    query = AuditLog.query
    if action:
        query = query.filter(AuditLog.action.like(f"{action}%"))
    logs = query.order_by(AuditLog.timestamp.desc()).limit(150).all()
    return render_template("audit.html", logs=logs, action=action)


@main_bp.route("/settings", methods=["GET", "POST"])
@login_required
@role_required("admin")
def settings():
    items = Setting.query.order_by(Setting.key).all()
    if request.method == "POST":
        try:
            for s in items:
                value = float(request.form.get(s.key, s.value))
                if value < 0:
                    raise ValueError
                s.value = value
        except ValueError:
            flash("Thresholds must be positive numbers.", "danger")
            return render_template("settings.html", settings=items), 400
        log_event("THRESHOLDS_UPDATED", "alert thresholds changed")
        db.session.commit()
        flash("Alert thresholds saved.", "success")
        return redirect(url_for("main.settings"))
    return render_template("settings.html", settings=items)


@main_bp.route("/metrics-count")
@login_required
def metrics_count():  # tiny health endpoint used in deployment smoke tests
    return {"metrics": Metric.query.count(), "devices": Device.query.count()}
