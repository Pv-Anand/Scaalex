from flask import Blueprint, jsonify, url_for, abort
from flask_login import login_required, current_user

from extensions import db, limiter
from models import ActionItem, Decision, Milestone, log_activity
from ai.notify_draft import draft_notify
from ai.anthropic_client import AIConfigError, AIRequestError

notify_bp = Blueprint("notify", __name__)


def _contact_first_name(client):
    contact = (
        client.contacts.filter_by(is_primary=True, portal_access=True).first()
        or client.contacts.filter_by(portal_access=True).first()
    )
    return contact.name.split()[0] if contact else "there"


def _portal_url(client):
    return url_for("portal.timeline", portal_slug=client.portal_slug, _external=True)


def _draft(item_type, state, item_title, client, extra_context=None):
    try:
        result = draft_notify(
            item_type, state, item_title, client.name,
            _contact_first_name(client), current_user.name.split()[0],
            _portal_url(client), extra_context=extra_context,
        )
    except AIConfigError as exc:
        return jsonify({"error": str(exc)}), 200
    except AIRequestError as exc:
        return jsonify({"error": str(exc)}), 502
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
