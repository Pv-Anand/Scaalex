import os
import uuid

from flask import Blueprint, render_template, request, redirect, url_for, flash, send_from_directory, current_app, abort
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename

from extensions import db
from models import Client, Document, Conversation, log_activity
from routes.clients import get_client_or_404

documents_bp = Blueprint("documents", __name__)


def _allowed(filename):
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return ext in current_app.config["ALLOWED_DOCUMENT_EXTENSIONS"]


@documents_bp.route("/documents")
@login_required
def global_list():
    q = Document.query
    client_id = request.args.get("client_id", type=int)
    if client_id:
        q = q.filter_by(client_id=client_id)
    documents = q.order_by(Document.uploaded_at.desc()).all()
    clients = Client.query.order_by(Client.name).all()
    return render_template("documents_global.html", documents=documents, clients=clients, current_client_id=client_id)


@documents_bp.route("/clients/<slug>/documents")
@login_required
def client_list(slug):
    client = get_client_or_404(slug)
    documents = client.documents.order_by(Document.uploaded_at.desc()).all()
    conversations = client.conversations.order_by(Conversation.date.desc()).all()
    return render_template(
        "documents_client.html", client=client, active_tab="documents",
        documents=documents, conversations=conversations,
    )


@documents_bp.route("/clients/<slug>/documents/upload", methods=["POST"])
@login_required
def upload(slug):
    client = get_client_or_404(slug)
    file = request.files.get("file")
    if not file or file.filename == "":
        flash("Choose a file to upload.", "error")
        return redirect(url_for("documents.client_list", slug=slug))
    if not _allowed(file.filename):
        flash("Unsupported file type.", "error")
        return redirect(url_for("documents.client_list", slug=slug))

    original_name = secure_filename(file.filename)
    ext = original_name.rsplit(".", 1)[-1].lower()
    stored_name = f"{uuid.uuid4().hex}.{ext}"
    path = os.path.join(current_app.config["DOCUMENT_UPLOAD_FOLDER"], stored_name)
    file.save(path)

    conversation_id = request.form.get("conversation_id", type=int)

    doc = Document(
        client_id=client.id,
        conversation_id=conversation_id or None,
        file_name=original_name,
        stored_name=stored_name,
        file_type=ext,
        file_size=os.path.getsize(path),
        uploaded_by_id=current_user.id,
    )
    db.session.add(doc)
    db.session.flush()
    log_activity(current_user.id, client.id, "Document uploaded", "document", doc.id, details=original_name)
    db.session.commit()
    flash(f"{original_name} uploaded.", "success")

    if conversation_id:
        return redirect(url_for("conversations.detail", slug=slug, conversation_id=conversation_id))
    return redirect(url_for("documents.client_list", slug=slug))


@documents_bp.route("/documents/<int:doc_id>/download")
@login_required
def download(doc_id):
    doc = Document.query.get_or_404(doc_id)
    return send_from_directory(
        current_app.config["DOCUMENT_UPLOAD_FOLDER"], doc.stored_name,
        as_attachment=True, download_name=doc.file_name,
    )


@documents_bp.route("/clients/<slug>/audio/<int:conversation_id>")
@login_required
def audio(slug, conversation_id):
    client = get_client_or_404(slug)
    conversation = Conversation.query.filter_by(id=conversation_id, client_id=client.id).first_or_404()
    if not conversation.audio_filename:
        abort(404)
    return send_from_directory(current_app.config["AUDIO_UPLOAD_FOLDER"], conversation.audio_filename)
