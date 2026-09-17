import secrets
from datetime import datetime, timedelta

from flask import Blueprint, redirect, url_for, request, flash, session
from flask_login import login_required, current_user

from extensions import db
from models import CalendarConnection, log_activity
from ai.google_calendar_client import (
    CalendarConfigError, CalendarRequestError,
    build_auth_url, exchange_code_for_tokens, refresh_access_token, fetch_user_email,
)

calendar_bp = Blueprint("calendar", __name__, url_prefix="/calendar")


def get_valid_access_token():
    """A valid access token for the firm's shared calendar connection, or
    None if nothing is connected. Refreshes and persists a new token if the
    current one is expired (or about to be) rather than letting a caller
    hit a 401 from Google."""
    conn = CalendarConnection.query.first()
    if not conn:
        return None

    if conn.token_expiry <= datetime.utcnow() + timedelta(minutes=2):
        try:
            tokens = refresh_access_token(conn.refresh_token)
        except CalendarRequestError:
            return None
        conn.access_token = tokens["access_token"]
        conn.token_expiry = datetime.utcnow() + timedelta(seconds=tokens.get("expires_in", 3600))
        db.session.commit()

    return conn.access_token


@calendar_bp.route("/connect")
@login_required
def connect():
    try:
        redirect_uri = url_for("calendar.oauth_callback", _external=True)
        state = secrets.token_urlsafe(24)
        session["calendar_oauth_state"] = state
        auth_url = build_auth_url(redirect_uri, state)
    except CalendarConfigError as exc:
        flash(str(exc), "error")
        return redirect(url_for("fireflies.inbox"))
    return redirect(auth_url)


@calendar_bp.route("/oauth/callback")
@login_required
def oauth_callback():
    error = request.args.get("error")
    if error:
        flash(f"Google Calendar connection was not completed: {error}", "error")
        return redirect(url_for("fireflies.inbox"))

    state = request.args.get("state")
    if not state or state != session.pop("calendar_oauth_state", None):
        flash("Calendar connection failed a security check - please try again.", "error")
        return redirect(url_for("fireflies.inbox"))

    code = request.args.get("code")
    if not code:
        flash("Google did not return an authorization code.", "error")
        return redirect(url_for("fireflies.inbox"))

    redirect_uri = url_for("calendar.oauth_callback", _external=True)
    try:
        tokens = exchange_code_for_tokens(code, redirect_uri)
        email = fetch_user_email(tokens["access_token"])
    except CalendarRequestError as exc:
        flash(f"Google Calendar connection failed: {exc}", "error")
        return redirect(url_for("fireflies.inbox"))

    expiry = datetime.utcnow() + timedelta(seconds=tokens.get("expires_in", 3600))

    # One shared connection for the firm (matches how Fireflies sync runs
    # against one shared workspace rather than per-advisor credentials) -
    # replace any existing connection rather than accumulating stale ones.
    CalendarConnection.query.delete()
    conn = CalendarConnection(
        email=email,
        access_token=tokens["access_token"],
        refresh_token=tokens.get("refresh_token", ""),
        token_expiry=expiry,
        connected_by_id=current_user.id,
    )
    db.session.add(conn)
    log_activity(current_user.id, None, "Google Calendar connected", "calendar_connection", details=email)
    db.session.commit()

    flash(f"Connected Google Calendar ({email}).", "success")
    return redirect(url_for("fireflies.inbox"))


@calendar_bp.route("/disconnect", methods=["POST"])
@login_required
def disconnect():
    CalendarConnection.query.delete()
    log_activity(current_user.id, None, "Google Calendar disconnected")
    db.session.commit()
    flash("Google Calendar disconnected.", "success")
    return redirect(url_for("fireflies.inbox"))
