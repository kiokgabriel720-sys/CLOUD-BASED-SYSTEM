"""Role-based access control helpers."""
from functools import wraps

from flask import abort
from flask_login import current_user

from .models import log_event
from . import db


def role_required(*roles):
    """Deny by default: only users holding one of ``roles`` may continue."""
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated:
                abort(401)
            if not current_user.has_role(*roles):
                log_event("AUTHZ_DENIED", f"{current_user.username} -> {view.__name__}")
                db.session.commit()
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return decorator
