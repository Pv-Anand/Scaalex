import re

from flask import Blueprint, jsonify, url_for, abort, request
from flask_login import login_required, current_user

from extensions import db, limiter
from models import ActionItem, CalendarConnection, Client, ClientContact, DataRoomFolder, Decision, Milestone, log_activity
from ai.notify_draft import draft_notify
from ai.anthropic_client import AIConfigError, AIRequestError
from ai.google_calendar_client import CalendarRequestError, scopes_allow_sending, send_gmail
from routes.calendar import get_valid_access_token

notify_bp = Blueprint("notify", __name__)


def _contact_first_name(client):
    contact = (
        client.contacts.filter_by(is_primary=True, portal_access=True).first()
        or client.contacts.filter_by(portal_access=True).first()
    )
    return contact.name.split()[0] if contact else "there"


def _portal_url(client, endpoint="portal.timeline"):
    return url_for(endpoint, portal_slug=client.portal_slug, _external=True)


def _draft(item_type, state, item_title, client, extra_context=None, endpoint="portal.timeline"):
    try:
        result = draft_notify(
            item_type, state, item_title, client.name,
            _contact_first_name(client), current_user.name.split()[0],
            _portal_url(client, endpoint), extra_context=extra_context,
        )
    except AIConfigError as exc:
        return jsonify({"error": str(exc)}), 200
    except AIRequestError as exc:
        return jsonify({"error": str(exc)}), 502

    # What the Send email panel needs: who can be emailed and whether a Google
    # account that may send is connected.
    conn = CalendarConnection.query.first()
    can_send = bool(conn and scopes_allow_sending(conn.scopes))
    contacts = [
        {"name": c.name, "email": c.email, "primary": bool(c.is_primary)}
        for c in client.contacts.order_by(ClientContact.is_primary.desc(), ClientContact.created_at.asc()).all()
        if c.email
    ]
    result = dict(result)
    result.update(
        client_id=client.id, contacts=contacts, connected=bool(conn), can_send=can_send,
        sender=conn.email if can_send else None, can_edit=current_user.can_edit_client(client.id),
    )
    return jsonify(result), 200


@notify_bp.route("/notify/action-item/<int:item_id>")
@login_required
@limiter.limit("20 per minute")
def action_item(item_id):
    item = ActionItem.query.get_or_404(item_id)
    if not current_user.can_view_client(item.client_id):
        abort(403)
    state = "completed" if item.status == "Completed" else "pending"
    resp = _draft("action item", state, item.task, item.client)
    log_activity(current_user.id, item.client_id, "Client notify draft generated", "action_item", item.id)
    db.session.commit()
    return resp


@notify_bp.route("/notify/decision/<int:decision_id>")
@login_required
@limiter.limit("20 per minute")
def decision(decision_id):
    d = Decision.query.get_or_404(decision_id)
    if not current_user.can_view_client(d.client_id):
        abort(403)
    state = "completed" if d.status == "Confirmed" else "pending"
    resp = _draft("decision", state, d.decision, d.client)
    log_activity(current_user.id, d.client_id, "Client notify draft generated", "decision", d.id)
    db.session.commit()
    return resp


@notify_bp.route("/notify/milestone/<int:milestone_id>")
@login_required
@limiter.limit("20 per minute")
def milestone(milestone_id):
    m = Milestone.query.get_or_404(milestone_id)
    if not current_user.can_view_client(m.client_id):
        abort(403)
    state = "completed" if m.status == "completed" else "pending"
    extra = f"We're waiting on: {m.active_request.message}" if m.active_request else None
    resp = _draft("milestone", state, m.title, m.client, extra_context=extra)
    log_activity(current_user.id, m.client_id, "Client notify draft generated", "milestone", m.id)
    db.session.commit()
    return resp


@notify_bp.route("/notify/data-room/<int:client_id>")
@login_required
@limiter.limit("20 per minute")
def data_room(client_id):
    client = Client.query.get_or_404(client_id)
    if not current_user.can_view_client(client.id):
        abort(403)
    resp = _draft("data room", "completed", "their Data Room is now available", client, endpoint="portal.data_room")
    log_activity(current_user.id, client.id, "Client notify draft generated", "data_room_folder", None)
    db.session.commit()
    return resp


@notify_bp.route("/notify/data-room-folder/<int:folder_id>")
@login_required
@limiter.limit("20 per minute")
def data_room_folder(folder_id):
    folder = DataRoomFolder.query.filter_by(id=folder_id, deleted_at=None).first_or_404()
    if not current_user.can_view_client(folder.client_id):
        abort(403)
    client = Client.query.get_or_404(folder.client_id)
    resp = _draft("data room folder", "completed", f'the "{folder.name}" folder in their Data Room', client, endpoint="portal.data_room")
    log_activity(current_user.id, client.id, "Client notify draft generated", "data_room_folder", folder.id)
    db.session.commit()
    return resp


EMAIL_RE = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+$")


def _addresses(value, limit):
    out = []
    for item in value or []:
        addr = str(item).strip().lower()
        if addr and addr not in out:
            out.append(addr)
    return out[:limit], out


def _fail(message, status=400, reconnect=False):
    return jsonify(ok=False, error=message, reconnect=reconnect), status


@notify_bp.route("/notify/send-email", methods=["POST"])
@login_required
@limiter.limit("10 per minute")
def send_email():
    """Send a reviewed Notify Client email from the connected Google account.
    Never automatic: the page asks for a confirmation click first."""
    data = request.get_json(silent=True) or {}
    client = Client.query.get(data.get("client_id") or 0)
    if not client:
        return _fail("Client not found.", 404)
    if not current_user.can_edit_client(client.id):
        return _fail("You need Edit access to this client to send email.", 403)

    to, to_all = _addresses(data.get("to"), 5)
    cc, cc_all = _addresses(data.get("cc"), 5)
    subject = str(data.get("subject") or "").strip()
    body = str(data.get("body") or "").strip()
    if not to:
        return _fail("Choose at least one recipient.")
    for addr in to + cc:
        if not EMAIL_RE.match(addr):
            return _fail(f'The address "{addr}" looks wrong. Please check it.')
    if len(to_all) > 5 or len(cc_all) > 5:
        return _fail("Too many recipients. Send to at most 5 people at once.")
    if not subject or len(subject) > 200:
        return _fail("Add a subject (up to 200 characters).")
    if not body or len(body) > 10000:
        return _fail("Add a message (up to 10,000 characters).")

    conn = CalendarConnection.query.first()
    if not conn:
        return _fail("Connect Google first to send email.", reconnect=True)
    if not scopes_allow_sending(conn.scopes):
        return _fail("Google needs you to reconnect and allow sending email.", reconnect=True)
    token = get_valid_access_token()
    if not token:
        return _fail("Google needs you to reconnect.", reconnect=True)

    bcc = [current_user.email.lower()] if data.get("bcc_me") and current_user.email else None
    try:
        send_gmail(token, conn.email, to, subject, body, cc=cc or None, bcc=bcc)
    except CalendarRequestError as exc:
        if str(exc) == "reconnect":
            return _fail("Google needs you to reconnect.", 401, reconnect=True)
        return _fail("Email was not sent. Nothing left the app. Try again in a minute.", 502)

    log_activity(
        current_user.id, client.id, "Client email sent", data.get("entity_type") or None, data.get("entity_id") or None,
        details=f"To {', '.join(to)}: {subject}"[:400],
    )
    db.session.commit()
    return jsonify(ok=True, sent_to=to, sender=conn.email)
