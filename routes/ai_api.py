"""JSON API endpoint for AI extraction from a conversation's notes/transcript.

Kept as its own blueprint (unprefixed, matching the /ai/* architecture called for
in the spec) since it's invoked via fetch() from the conversation detail page
rather than rendering a page itself.
"""
from flask import Blueprint, request, jsonify
from flask_login import login_required, current_user

from extensions import db
from models import Conversation, log_activity
from ai.anthropic_client import AIConfigError, AIRequestError
from ai.extract import extract_from_text

ai_api_bp = Blueprint("ai_api", __name__, url_prefix="/ai")


@ai_api_bp.route("/extract", methods=["POST"])
@login_required
def extract():
    payload = request.get_json(force=True)
    conversation_id = payload.get("conversation_id")
    conversation = Conversation.query.get_or_404(conversation_id)
    client = conversation.client

    source_text = (payload.get("source_text") or conversation.transcript or conversation.raw_notes or "").strip()
    if not source_text:
        return jsonify({"error": "There is no transcript or notes to extract from yet."}), 400

    try:
        result = extract_from_text(
            client.name, conversation.interaction_type, conversation.participants,
            conversation.date.strftime("%d %b %Y"), source_text,
        )
    except AIConfigError as exc:
        return jsonify({"error": str(exc), "config_error": True}), 200
    except AIRequestError as exc:
        return jsonify({"error": str(exc)}), 502

    conversation.ai_extraction = result
    conversation.extraction_status = "pending_review"
    log_activity(current_user.id, client.id, "AI extraction generated", "conversation", conversation.id)
    db.session.commit()

    return jsonify(result)
