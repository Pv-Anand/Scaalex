import os
import uuid

from flask import Blueprint, render_template, request, redirect, url_for, flash, send_from_directory, current_app
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename

from extensions import db
from models import Client, Document, Conversation, Milestone, log_activity
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
    all_documents = client.documents.order_by(Document.uploaded_at.desc()).all()

    source = request.args.get("source")
    milestone_id = request.args.get("milestone_id", type=int)
    documents = all_documents
    if source == "scaalex":
        documents = [d for d in documents if d.uploader_role == "Scaalex"]
    elif source == "client":
        documents = [d for d in documents if d.uploader_role == "Client"]
    if milestone_id:
        documents = [d for d in documents if d.milestone_id == milestone_id]

    conversations = client.conversations.order_by(Conversation.date.desc()).all()
    milestones = client.milestones.order_by(Milestone.title).all()

    from_scaalex = sum(1 for d in all_documents if d.uploader_role == "Scaalex")
    from_client = sum(1 for d in all_documents if d.uploader_role == "Client")

    return render_template(
        "documents_client.html", client=client, active_tab="documents",
        documents=documents, conversations=conversations, milestones=milestones,
        total_count=len(all_documents), from_scaalex=from_scaalex, from_client=from_client,
        current_source=source, current_milestone_id=milestone_id,
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
    milestone_id = request.form.get("milestone_id", type=int)
    if milestone_id and not Milestone.query.filter_by(id=milestone_id, client_id=client.id).first():
        milestone_id = None

    doc = Document(
        client_id=client.id,
        conversation_id=conversation_id or None,
        milestone_id=milestone_id or None,
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
