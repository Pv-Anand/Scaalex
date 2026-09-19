from functools import wraps

from flask import abort
from flask_login import current_user


def admin_required(view):
    """Gate a route to Owners and Administrators. Always pair with
    @login_required (placed above this) so current_user is guaranteed to be
    a real User, not Flask-Login's AnonymousUserMixin."""

    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user.is_admin:
            abort(403)
        return view(*args, **kwargs)

    return wrapped
