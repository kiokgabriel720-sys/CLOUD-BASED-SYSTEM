"""Authentication: login, logout, account lockout and password change."""
from datetime import timedelta
from urllib.parse import urlparse

from flask import (Blueprint, current_app, flash, redirect, render_template,
                   request, url_for)
from flask_login import current_user, login_required, login_user, logout_user

from . import db
from .models import User, log_event, utcnow

auth_bp = Blueprint("auth", __name__)


def _safe_next(target):
    """Only allow redirects to local paths (prevents open-redirect attacks)."""
    if not target:
        return None
    parts = urlparse(target)
    return target if not parts.netloc and not parts.scheme and target.startswith("/") else None


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()
        password = request.form.get("password", "")
        if not username or not password:
            flash("Username and password are required.", "danger")
            return render_template("login.html", username=username), 400

        user = User.query.filter_by(username=username).first()
        now = utcnow()

        if user and user.locked_until and user.locked_until > now:
            log_event("LOGIN_LOCKED", f"attempt on locked account {username}", user=user)
            db.session.commit()
            flash("Account temporarily locked after repeated failed attempts. "
                  "Try again later.", "danger")
            return render_template("login.html", username=username), 403

        if user is None or not user.check_password(password) or not user.active:
            if user:
                user.failed_logins += 1
                if user.failed_logins >= current_app.config["MAX_FAILED_LOGINS"]:
                    user.locked_until = now + timedelta(
                        minutes=current_app.config["LOCKOUT_MINUTES"])
                    user.failed_logins = 0
                    log_event("ACCOUNT_LOCKED", f"{username} locked", user=user)
            log_event("LOGIN_FAILED", f"username={username}", user=user)
            db.session.commit()
            # Generic message: do not reveal whether the username exists
            flash("Invalid username or password.", "danger")
            return render_template("login.html", username=username), 401

        user.failed_logins = 0
        user.locked_until = None
        user.last_login = now
        login_user(user, remember=bool(request.form.get("remember")))
        log_event("LOGIN_SUCCESS", f"{user.username} signed in", user=user)
        db.session.commit()
        flash(f"Welcome back, {user.full_name.split()[0]}.", "success")
        return redirect(_safe_next(request.args.get("next")) or url_for("main.dashboard"))

    return render_template("login.html")


@auth_bp.app_context_processor
def inject_default_login():
    """Show the first-run credentials on the login page until they are changed."""
    from flask import request as _rq
    if _rq.endpoint != "auth.login":
        return {}
    from .models import default_password_in_use
    if default_password_in_use(current_app):
        return {"default_login": (current_app.config["DEFAULT_ADMIN_USERNAME"],
                                  current_app.config["DEFAULT_ADMIN_PASSWORD"])}
    return {}


@auth_bp.route("/logout")
@login_required
def logout():
    log_event("LOGOUT", f"{current_user.username} signed out")
    db.session.commit()
    logout_user()
    flash("You have been logged out.", "success")
    return redirect(url_for("auth.login"))


@auth_bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    if request.method == "POST":
        current = request.form.get("current_password", "")
        new = request.form.get("new_password", "")
        confirm = request.form.get("confirm_password", "")
        if not current_user.check_password(current):
            flash("Current password is incorrect.", "danger")
        elif len(new) < 8 or new.isalpha() or new.isdigit():
            flash("New password must be at least 8 characters and mix letters "
                  "and numbers.", "danger")
        elif new != confirm:
            flash("New passwords do not match.", "danger")
        else:
            current_user.set_password(new)
            log_event("PASSWORD_CHANGED", current_user.username)
            db.session.commit()
            flash("Password updated successfully.", "success")
            return redirect(url_for("auth.profile"))
    return render_template("profile.html")
