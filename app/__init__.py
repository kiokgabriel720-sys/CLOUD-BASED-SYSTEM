"""Application factory for the Cloud-Based Network Management System (CloudNMS)."""
import os
from datetime import timezone
from zoneinfo import ZoneInfo

from flask import Flask, render_template
from flask_login import LoginManager
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect

db = SQLAlchemy()
login_manager = LoginManager()
csrf = CSRFProtect()


def create_app(config_object="config.Config"):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(config_object)
    os.makedirs(app.instance_path, exist_ok=True)

    db.init_app(app)
    csrf.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message = "Please log in to access the management console."
    login_manager.login_message_category = "warning"

    from . import models  # noqa: F401  (register models)
    from .auth import auth_bp
    from .main import main_bp
    from .api import api_bp
    from .cli import register_cli

    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(api_bp, url_prefix="/api/v1")
    csrf.exempt(api_bp)  # agents authenticate with per-device API keys
    register_cli(app)

    tz = ZoneInfo(app.config["DISPLAY_TIMEZONE"])

    @app.template_filter("localtime")
    def localtime(value, fmt="%d %b %Y %H:%M"):
        if value is None:
            return "—"
        return value.replace(tzinfo=timezone.utc).astimezone(tz).strftime(fmt)

    @app.template_filter("prevday")
    def prevday(value):
        """Report periods are stored end-exclusive; display the inclusive end date."""
        from datetime import timedelta
        return (value - timedelta(days=1)).date()

    @app.template_filter("ago")
    def ago(value):
        from .models import utcnow
        if value is None:
            return "never"
        secs = int((utcnow() - value).total_seconds())
        if secs < 60:
            return f"{secs}s ago"
        if secs < 3600:
            return f"{secs // 60} min ago"
        if secs < 86400:
            return f"{secs // 3600} h ago"
        return f"{secs // 86400} d ago"

    @app.context_processor
    def inject_globals():
        from flask_login import current_user
        from .models import Alert
        open_alerts = 0
        if current_user.is_authenticated:
            open_alerts = Alert.query.filter(Alert.status != "resolved").count()
        return {"open_alert_count": open_alerts}

    @app.errorhandler(403)
    def forbidden(_e):
        return render_template("error.html", code=403,
                               message="You do not have permission to access this page."), 403

    @app.errorhandler(404)
    def not_found(_e):
        return render_template("error.html", code=404,
                               message="The page you requested was not found."), 404

    with app.app_context():
        db.create_all()
        from .models import ensure_default_admin, ensure_default_settings
        ensure_default_settings()
        ensure_default_admin(app)

    return app
