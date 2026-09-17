"""JSON API endpoints for the audio -> transcript -> structured extraction pipeline.

Kept as its own blueprint (unprefixed, matching the /ai/* architecture called for
in the spec) since it's invoked via fetch() from the conversation detail page
rather than rendering a page itself.
"""
import os
import uuid

from flask import Blueprint, request, jsonify, current_app
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename

from extensions import db
from models import Conversation, log_activity
from ai.anthropic_client import AIConfigError, AIRequestError
from ai.transcribe import transcribe_audio_file
from ai.extract import extract_from_text

ai_api_bp = Blueprint("ai_api", __name__, url_prefix="/ai")


def _allowed_audio(filename):
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return ext in current_app.config["ALLOWED_AUDIO_EXTENSIONS"]


@ai_api_bp.route("/transcribe", methods=["POST"])
@login_required
def transcribe():
    conversation_id = request.form.get("conversation_id", type=int)
    conversation = Conversation.query.get_or_404(conversation_id)

    file = request.files.get("audio")
    if not file or file.filename == "":
        return jsonify({"error": "No audio file provided."}), 400
    if not _allowed_audio(file.filename):
        return jsonify({"error": "Unsupported file type. Use MP3, WAV, M4A, or MP4."}), 400

    ext = file.filename.rsplit(".", 1)[-1].lower()
    stored_name = f"{uuid.uuid4().hex}.{ext}"
    path = os.path.join(current_app.config["AUDIO_UPLOAD_FOLDER"], stored_name)
    file.save(path)

    conversation.audio_filename = stored_name
    conversation.audio_original_name = secure_filename(file.filename)
    conversation.transcript_status = "pending"
    db.session.commit()

    try:
        transcript = transcribe_audio_file(path)
    except AIConfigError as exc:
        conversation.transcript_status = "none"
        db.session.commit()
        return jsonify({"error": str(exc), "config_error": True}), 200
    except AIRequestError as exc:
        conversation.transcript_status = "failed"
        db.session.commit()
        return jsonify({"error": str(exc)}), 502

    conversation.transcript = transcript
    conversation.transcript_status = "ready"
    log_activity(current_user.id, conversation.client_id, "Voice recording transcribed",
                 "conversation", conversation.id)
    db.session.commit()

    return jsonify({"transcript": transcript})


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
