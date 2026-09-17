from flask import Blueprint, render_template
from flask_login import login_required

from models import Client, Conversation, Decision, ActionItem

home_bp = Blueprint("home", __name__)


@home_bp.route("/")
@login_required
def index():
    clients = Client.query.order_by(Client.name).all()

    recent_conversations = (
        Conversation.query.order_by(Conversation.date.desc()).limit(6).all()
    )
    pending_actions = (
        ActionItem.query.filter(ActionItem.status != "Completed")
        .order_by(ActionItem.due_date.is_(None), ActionItem.due_date.asc())
        .limit(8)
        .all()
    )
    recent_decisions = Decision.query.order_by(Decision.date.desc()).limit(6).all()

    total_open_actions = ActionItem.query.filter(ActionItem.status != "Completed").count()

    return render_template(
        "home.html",
        clients=clients,
        recent_conversations=recent_conversations,
        pending_actions=pending_actions,
        recent_decisions=recent_decisions,
        total_open_actions=total_open_actions,
    )
