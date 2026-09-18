from datetime import datetime

from flask import Blueprint, render_template
from flask_login import login_required

from models import Client, Conversation, Decision, ActionItem

home_bp = Blueprint("home", __name__)

PRIORITY_ORDER = {"High": 0, "Medium": 1, "Low": 2}


@home_bp.route("/")
@login_required
def index():
    clients = Client.query.order_by(Client.name).all()

    recent_conversations = (
        Conversation.query.order_by(Conversation.date.desc()).limit(6).all()
    )

    open_items = (
        ActionItem.query.filter(ActionItem.status != "Completed")
        .order_by(ActionItem.due_date.is_(None), ActionItem.due_date.asc())
        .all()
    )

    overdue_items = [a for a in open_items if a.is_overdue]
    high_priority_items = [a for a in open_items if a.priority == "High"]

    needs_attention_ids = {}
    for a in overdue_items + high_priority_items:
        needs_attention_ids[a.id] = a
    needs_attention = sorted(
        needs_attention_ids.values(),
        key=lambda a: (
            0 if a.is_overdue else 1,
            PRIORITY_ORDER.get(a.priority, 3),
            a.due_date or datetime.max.date(),
        ),
    )[:6]

    pending_by_priority = {"High": [], "Medium": [], "Low": []}
    for a in open_items:
        pending_by_priority.setdefault(a.priority, []).append(a)
    for key in pending_by_priority:
        pending_by_priority[key] = pending_by_priority[key][:6]

    overdue_by_client = {}
    for a in overdue_items:
        overdue_by_client[a.client_id] = overdue_by_client.get(a.client_id, 0) + 1

    recent_decisions = Decision.query.order_by(Decision.date.desc()).limit(6).all()

    month_start = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    decisions_this_month = Decision.query.filter(Decision.date >= month_start).count()

    active_clients_count = sum(1 for c in clients if c.status == "Active")

    return render_template(
        "home.html",
        clients=clients,
        recent_conversations=recent_conversations,
        needs_attention=needs_attention,
        pending_by_priority=pending_by_priority,
        recent_decisions=recent_decisions,
        total_open_actions=len(open_items),
        overdue_count=len(overdue_items),
        high_priority_count=len(high_priority_items),
        decisions_this_month=decisions_this_month,
        active_clients_count=active_clients_count,
        overdue_by_client=overdue_by_client,
    )
