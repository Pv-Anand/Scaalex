from flask import Blueprint, render_template, request
from flask_login import login_required

from models import Conversation, Decision, ActionItem
from routes.clients import get_client_or_404

search_bp = Blueprint("search", __name__)


@search_bp.route("/clients/<slug>/search")
@login_required
def client_search(slug):
    client = get_client_or_404(slug)
    query = request.args.get("q", "").strip()

    conversations, decisions, action_items = [], [], []

    if query:
        like = f"%{query}%"
        conversations = (
            Conversation.query.filter(Conversation.client_id == client.id)
            .filter(
                db_or(
                    Conversation.raw_notes.ilike(like),
                    Conversation.summary.ilike(like),
                    Conversation.transcript.ilike(like),
                    Conversation.important_context.ilike(like),
                )
            )
            .order_by(Conversation.date.desc())
            .all()
        )
        # participant match (JSON text column, do a python-side filter too)
        all_convos = Conversation.query.filter_by(client_id=client.id).all()
        for c in all_convos:
            if c in conversations:
                continue
            if any(query.lower() in p.lower() for p in (c.participants or [])):
                conversations.append(c)

        decisions = (
            Decision.query.filter(Decision.client_id == client.id)
            .filter(db_or(Decision.decision.ilike(like), Decision.context.ilike(like), Decision.owner.ilike(like)))
            .order_by(Decision.date.desc())
            .all()
        )
        action_items = (
            ActionItem.query.filter(ActionItem.client_id == client.id)
            .filter(db_or(ActionItem.task.ilike(like), ActionItem.owner.ilike(like)))
            .all()
        )

    return render_template(
        "search_results.html", client=client, active_tab="overview",
        query=query, conversations=conversations, decisions=decisions, action_items=action_items,
    )


def db_or(*args):
    from sqlalchemy import or_
    return or_(*args)
