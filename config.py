"""Application configuration for the Cloud-Based Network Management System."""
import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))


class Config:
    # Secrets are read from the environment in production (never hard-coded).
    SECRET_KEY = os.environ.get("NMS_SECRET_KEY", "dev-only-change-me")
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "NMS_DATABASE_URL",
        "sqlite:///" + os.path.join(BASE_DIR, "instance", "cloudnms.db"),
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    # Wait up to 30 s for another process (e.g. the simulator) to finish writing
    SQLALCHEMY_ENGINE_OPTIONS = {"connect_args": {"timeout": 30}}

    # Session hardening
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.environ.get("NMS_SECURE_COOKIES", "0") == "1"
    REMEMBER_COOKIE_HTTPONLY = True
    PERMANENT_SESSION_LIFETIME = 60 * 60 * 8  # 8 hours

    # Monitoring behaviour
    HEARTBEAT_TIMEOUT_SECONDS = int(os.environ.get("NMS_HEARTBEAT_TIMEOUT", 180))
    MAX_FAILED_LOGINS = 5
    LOCKOUT_MINUTES = 15
    DISPLAY_TIMEZONE = "Africa/Nairobi"

    # First-run account: created automatically when the database has no users.
    CREATE_DEFAULT_ADMIN = True
    DEFAULT_ADMIN_USERNAME = os.environ.get("NMS_ADMIN_USERNAME", "admin")
    DEFAULT_ADMIN_PASSWORD = os.environ.get("NMS_ADMIN_PASSWORD", "Admin@2026")


class TestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_ENGINE_OPTIONS = {}
    WTF_CSRF_ENABLED = False
    CREATE_DEFAULT_ADMIN = False
    SECRET_KEY = "test"
