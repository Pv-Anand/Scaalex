"""The client-facing portal - deliberately NOT built on Flask-Login/current_user.

A client contact is not a staff User: no role, no access to any other
client, no access to the internal app at all. Keeping this as its own
session key (`portal_contact_id`) rather than extending Flask-Login for a
second audience means there's no code path where a client session could
accidentally satisfy a staff @login_required check, or vice versa.
"""
import os
import uuid
from datetime import datetime
from functools import wraps

from flask import Blueprint, render_template, request, redirect, url_for, flash, session, abort, current_app, send_from_directory
from werkzeug.utils import secure_filename

from extensions import db, limiter
from models import Client, ClientContact, Milestone, MilestoneRequest, Document, log_portal_activity
from routes.documents import _allowed

portal_bp = Blueprint("portal", __name__, url_prefix="/portal/<portal_slug>")


def _get_portal_client(portal_slug):
    client = Client.query.filter_by(portal_slug=portal_slug).first()
    if not client:
        abort(404)
    return client


def portal_login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        portal_slug = kwargs.get("portal_slug")
        client = _get_portal_client(portal_slug)
        contact = ClientContact.query.get(session.get("portal_contact_id") or 0)
        if not contact or contact.client_id != client.id or not contact.portal_access:
            session.pop("portal_contact_id", None)
            flash("Please sign in to continue.", "info")
            return redirect(url_for("portal.login", portal_slug=portal_slug))
        if contact.must_change_password and f.__name__ != "set_password":
            return redirect(url_for("portal.set_password", portal_slug=portal_slug))
        kwargs["client"] = client
        kwargs["contact"] = contact
        return f(*args, **kwargs)
    return wrapper


@portal_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute", methods=["POST"])
def login(portal_slug):
    client = _get_portal_client(portal_slug)

    existing = ClientContact.query.get(session.get("portal_contact_id") or 0)
    if existing and existing.client_id == client.id and existing.portal_access:
        return redirect(url_for("portal.timeline", portal_slug=portal_slug))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "").strip()
        contact = ClientContact.query.filter_by(client_id=client.id, portal_access=True).filter(
            db.func.lower(ClientContact.email) == email
        ).first()

        if contact and contact.check_password(password):
            # Only touch our own session key - never clear() the whole
            # session, since a staff member testing the portal in the same
            # browser would otherwise get silently signed out of the app.
            session["portal_contact_id"] = contact.id
            contact.last_login_at = datetime.utcnow()
            log_portal_activity(contact.id, client.id, "Signed in", "client_contact", contact.id)
            db.session.commit()
            if contact.must_change_password:
                return redirect(url_for("portal.set_password", portal_slug=portal_slug))
            return redirect(url_for("portal.timeline", portal_slug=portal_slug))

        flash("Invalid email or mobile number.", "error")

    return render_template("portal_login.html", client=client)


@portal_bp.route("/set-password", methods=["GET", "POST"])
@portal_login_required
def set_password(portal_slug, client, contact):
    if not contact.must_change_password:
        return redirect(url_for("portal.timeline", portal_slug=portal_slug))

    if request.method == "POST":
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")
        if len(new_password) < 8:
            flash("Password must be at least 8 characters.", "error")
        elif new_password != confirm_password:
            flash("Passwords don't match.", "error")
        else:
            contact.set_password(new_password)
            contact.must_change_password = False
            log_portal_activity(contact.id, client.id, "Password set", "client_contact", contact.id)
            db.session.commit()
            flash("Password set.", "success")
            return redirect(url_for("portal.timeline", portal_slug=portal_slug))

    return render_template("portal_set_password.html", client=client)


@portal_bp.route("/logout")
def logout(portal_slug):
    session.pop("portal_contact_id", None)
    return redirect(url_for("portal.login", portal_slug=portal_slug))


@portal_bp.route("/")
@portal_login_required
def timeline(portal_slug, client, contact):
    completed = (
        client.milestones.filter_by(status="completed", visible_to_client=True)
        .order_by(Milestone.date.desc()).limit(5).all()
    )
    next_up = (
        client.milestones.filter_by(status="in_progress", visible_to_client=True)
        .order_by(Milestone.due_date.asc()).first()
    )
    ahead = (
        client.milestones.filter_by(status="upcoming", visible_to_client=True)
        .order_by(Milestone.due_date.asc()).all()
    )
    total_visible = client.milestones.filter_by(visible_to_client=True).count()
    completed_count = client.milestones.filter_by(status="completed", visible_to_client=True).count()

    return render_template(
        "portal_timeline.html", client=client, contact=contact,
        completed=completed, next_up=next_up, ahead=ahead,
        total_visible=total_visible, completed_count=completed_count,
    )


@portal_bp.route("/requests/<int:request_id>/respond", methods=["POST"])
@portal_login_required
def respond_to_request(portal_slug, client, contact, request_id):
    req = MilestoneRequest.query.join(Milestone).filter(
        MilestoneRequest.id == request_id, Milestone.client_id == client.id,
    ).first_or_404()

    if req.status != "awaiting":
        flash("This request has already been answered.", "info")
        return redirect(url_for("portal.timeline", portal_slug=portal_slug))

    url_value = request.form.get("response_url", "").strip()
    text_value = request.form.get("response_text", "").strip()
    file = request.files.get("file")

    if file and file.filename:
        if not _allowed(file.filename):
            flash("Unsupported file type.", "error")
            return redirect(url_for("portal.timeline", portal_slug=portal_slug))
        original_name = secure_filename(file.filename)
        ext = original_name.rsplit(".", 1)[-1].lower()
        stored_name = f"{uuid.uuid4().hex}.{ext}"
        path = os.path.join(current_app.config["DOCUMENT_UPLOAD_FOLDER"], stored_name)
        file.save(path)
        doc = Document(
            client_id=client.id, milestone_id=req.milestone_id,
            file_name=original_name, stored_name=stored_name, file_type=ext,
            file_size=os.path.getsize(path), uploaded_by_contact_id=contact.id,
        )
        db.session.add(doc)
        db.session.flush()
        req.response_document_id = doc.id
        log_portal_activity(contact.id, client.id, "Uploaded file", "document", doc.id, details=original_name)
    elif url_value:
        req.response_url = url_value
        log_portal_activity(contact.id, client.id, "Shared a link", "milestone_request", req.id, details=url_value)
    elif text_value:
        req.response_text = text_value
        log_portal_activity(contact.id, client.id, "Provided data", "milestone_request", req.id, details=text_value[:200])
    else:
        flash("Add a file, a link, or a short answer first.", "error")
        return redirect(url_for("portal.timeline", portal_slug=portal_slug))

    req.status = "fulfilled"
    req.fulfilled_by_contact_id = contact.id
    req.fulfilled_at = datetime.utcnow()
    db.session.commit()
    flash("Sent to Scaalex.", "success")
    return redirect(url_for("portal.timeline", portal_slug=portal_slug))


@portal_bp.route("/documents/<int:doc_id>/download")
@portal_login_required
def download(portal_slug, client, contact, doc_id):
    doc = Document.query.filter_by(id=doc_id, client_id=client.id).first_or_404()
    log_portal_activity(contact.id, client.id, "Downloaded file", "document", doc.id, details=doc.file_name)
    db.session.commit()
    return send_from_directory(
        current_app.config["DOCUMENT_UPLOAD_FOLDER"], doc.stored_name,
        as_attachment=True, download_name=doc.file_name,
    )
