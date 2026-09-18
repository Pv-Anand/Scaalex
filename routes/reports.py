"""Reports & Logs tabs - the internal side of the client portal.

Reports controls what a client sees (portal access per contact, and the
milestone timeline that drives their view); Logs shows what actually
happened - both client-side portal activity and the staff actions that
affect it - in one merged feed via AuditLog.
"""
import os
import re
import uuid
from datetime import datetime

from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename

from extensions import db
from models import (
    Client, ClientContact, Milestone, MilestoneRequest, Document, User, AuditLog,
    log_activity,
)
from routes.clients import get_client_or_404
from routes.documents import _allowed

reports_bp = Blueprint("reports", __name__, url_prefix="/clients/<slug>")

MILESTONE_STATUSES = ["upcoming", "in_progress", "completed"]
REQUEST_TYPES = [("data", "Data"), ("url", "URL"), ("document", "Document")]


# These are routes on the unified portal_hub_bp (/portal/login, etc.) - a
# client's portal_slug can never take one of these, or it would shadow that
# route at /portal/<slug>.
RESERVED_PORTAL_SLUGS = {"login", "choose", "logout", ""}


def _slugify_portal(text, client_id=None):
    base = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "client"
    if base in RESERVED_PORTAL_SLUGS:
        base = f"{base}-portal"
    slug = base
    i = 2
    while True:
        existing = Client.query.filter_by(portal_slug=slug).first()
        if not existing or existing.id == client_id:
            return slug
        slug = f"{base}-{i}"
        i += 1


def _ensure_portal_slug(client):
    if not client.portal_slug:
        client.portal_slug = _slugify_portal(f"{client.slug}-{datetime.utcnow().year}", client.id)
        db.session.commit()


@reports_bp.route("/reports")
@login_required
def reports(slug):
    client = get_client_or_404(slug)
    _ensure_portal_slug(client)

    contacts = client.contacts.order_by(ClientContact.is_primary.desc(), ClientContact.created_at.asc()).all()

    completed = (
        client.milestones.filter_by(status="completed")
        .order_by(Milestone.date.desc()).all()
    )
    in_progress = (
        client.milestones.filter_by(status="in_progress")
        .order_by(Milestone.due_date.asc()).all()
    )
    upcoming = (
        client.milestones.filter_by(status="upcoming")
        .order_by(Milestone.due_date.asc()).all()
    )
    milestones = completed + in_progress + upcoming

    return render_template(
        "reports.html", client=client, active_tab="reports",
        contacts=contacts, milestones=milestones,
    )


@reports_bp.route("/reports/portal-url", methods=["POST"])
@login_required
def edit_portal_url(slug):
    client = get_client_or_404(slug)
    raw = request.form.get("portal_slug", "").strip()
    if not raw:
        flash("Portal URL can't be empty.", "error")
        return redirect(url_for("reports.reports", slug=slug))

    new_slug = _slugify_portal(raw, client.id)
    client.portal_slug = new_slug
    log_activity(current_user.id, client.id, "Portal URL changed", "client", client.id, details=new_slug)
    db.session.commit()
    flash("Portal URL updated.", "success")
    return redirect(url_for("reports.reports", slug=slug))


@reports_bp.route("/reports/contacts/<int:contact_id>/access", methods=["POST"])
@login_required
def toggle_access(slug, contact_id):
    client = get_client_or_404(slug)
    contact = ClientContact.query.filter_by(id=contact_id, client_id=client.id).first_or_404()

    turning_on = not contact.portal_access
    if turning_on:
        if not contact.email or not contact.phone:
            flash(f"{contact.name} needs both an email and a phone number on Client Profile before granting portal access.", "error")
            return redirect(url_for("reports.reports", slug=slug))
        contact.set_password(contact.phone.strip())
        contact.must_change_password = True
        contact.portal_access = True
        log_activity(current_user.id, client.id, "Portal access granted", "client_contact", contact.id, details=contact.name)
        flash(f"Portal access granted to {contact.name}. They can sign in with their email and mobile number.", "success")
    else:
        contact.portal_access = False
        log_activity(current_user.id, client.id, "Portal access revoked", "client_contact", contact.id, details=contact.name)
        flash(f"Portal access revoked for {contact.name}.", "success")

    db.session.commit()
    return redirect(url_for("reports.reports", slug=slug))


@reports_bp.route("/reports/milestones/new", methods=["GET", "POST"])
@login_required
def new_milestone(slug):
    client = get_client_or_404(slug)
    managers = User.query.order_by(User.name).all()

    if request.method == "POST":
        title = request.form.get("title", "").strip()
        if not title:
            flash("Milestone title is required.", "error")
            return redirect(url_for("reports.new_milestone", slug=slug))

        status = request.form.get("status", "upcoming")
        if status not in MILESTONE_STATUSES:
            status = "upcoming"

        date_val = _parse_date(request.form.get("date"))
        due_date_val = _parse_date(request.form.get("due_date"))
        manager_id = request.form.get("reporting_manager_id", type=int)

        milestone = Milestone(
            client_id=client.id,
            title=title,
            status=status,
            date=(date_val or datetime.utcnow().date()) if status == "completed" else None,
            due_date=due_date_val,
            reporting_manager_id=manager_id or None,
            visible_to_client=bool(request.form.get("visible_to_client")),
            created_by_id=current_user.id,
        )
        db.session.add(milestone)
        db.session.flush()
        log_activity(current_user.id, client.id, "Milestone added", "milestone", milestone.id, details=title)
        db.session.commit()
        flash("Milestone added.", "success")
        return redirect(url_for("reports.reports", slug=slug))

    return render_template("milestone_new.html", client=client, active_tab="reports", managers=managers)


def _parse_date(raw):
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return None


@reports_bp.route("/reports/milestones/<int:milestone_id>")
@login_required
def milestone_detail(slug, milestone_id):
    client = get_client_or_404(slug)
    milestone = Milestone.query.filter_by(id=milestone_id, client_id=client.id).first_or_404()
    managers = User.query.order_by(User.name).all()
    deliverables = milestone.deliverables.all()
    return render_template(
        "milestone_detail.html", client=client, active_tab="reports",
        milestone=milestone, managers=managers, deliverables=deliverables,
    )


@reports_bp.route("/reports/milestones/<int:milestone_id>/edit", methods=["POST"])
@login_required
def edit_milestone(slug, milestone_id):
    client = get_client_or_404(slug)
    milestone = Milestone.query.filter_by(id=milestone_id, client_id=client.id).first_or_404()

    milestone.title = request.form.get("title", milestone.title).strip() or milestone.title
    status = request.form.get("status", milestone.status)
    if status in MILESTONE_STATUSES:
        milestone.status = status
    milestone.due_date = _parse_date(request.form.get("due_date"))
    if status == "completed":
        milestone.date = _parse_date(request.form.get("date")) or milestone.date or datetime.utcnow().date()
    manager_id = request.form.get("reporting_manager_id", type=int)
    milestone.reporting_manager_id = manager_id or None
    milestone.visible_to_client = bool(request.form.get("visible_to_client"))

    log_activity(current_user.id, client.id, "Milestone updated", "milestone", milestone.id, details=milestone.title)
    db.session.commit()
    flash("Milestone updated.", "success")
    return redirect(url_for("reports.milestone_detail", slug=slug, milestone_id=milestone.id))


@reports_bp.route("/reports/milestones/<int:milestone_id>/request", methods=["POST"])
@login_required
def send_request(slug, milestone_id):
    client = get_client_or_404(slug)
    milestone = Milestone.query.filter_by(id=milestone_id, client_id=client.id).first_or_404()

    if milestone.active_request:
        flash("There's already an open request on this milestone. Wait for a response, or it'll show as fulfilled once one comes in.", "error")
        return redirect(url_for("reports.milestone_detail", slug=slug, milestone_id=milestone.id))

    request_type = request.form.get("request_type", "data")
    if request_type not in dict(REQUEST_TYPES):
        request_type = "data"
    message = request.form.get("message", "").strip()
    if not message:
        flash("Describe what you need from the client.", "error")
        return redirect(url_for("reports.milestone_detail", slug=slug, milestone_id=milestone.id))

    req = MilestoneRequest(
        milestone_id=milestone.id, request_type=request_type, message=message,
        requested_by_id=current_user.id,
    )
    db.session.add(req)
    db.session.flush()
    log_activity(current_user.id, client.id, "Requested from client", "milestone_request", req.id, details=f"{milestone.title}: {message}")
    db.session.commit()
    flash("Request sent — it now shows on the client's portal.", "success")
    return redirect(url_for("reports.milestone_detail", slug=slug, milestone_id=milestone.id))


@reports_bp.route("/reports/milestones/<int:milestone_id>/deliverable", methods=["POST"])
@login_required
def attach_deliverable(slug, milestone_id):
    client = get_client_or_404(slug)
    milestone = Milestone.query.filter_by(id=milestone_id, client_id=client.id).first_or_404()

    file = request.files.get("file")
    if not file or file.filename == "":
        flash("Choose a file to attach.", "error")
        return redirect(url_for("reports.milestone_detail", slug=slug, milestone_id=milestone.id))
    if not _allowed(file.filename):
        flash("Unsupported file type.", "error")
        return redirect(url_for("reports.milestone_detail", slug=slug, milestone_id=milestone.id))

    original_name = secure_filename(file.filename)
    ext = original_name.rsplit(".", 1)[-1].lower()
    stored_name = f"{uuid.uuid4().hex}.{ext}"
    path = os.path.join(current_app.config["DOCUMENT_UPLOAD_FOLDER"], stored_name)
    file.save(path)

    doc = Document(
        client_id=client.id, milestone_id=milestone.id,
        file_name=original_name, stored_name=stored_name, file_type=ext,
        file_size=os.path.getsize(path), uploaded_by_id=current_user.id,
    )
    db.session.add(doc)
    db.session.flush()
    log_activity(current_user.id, client.id, "Deliverable attached", "document", doc.id, details=f"{milestone.title}: {original_name}")
    db.session.commit()
    flash(f"{original_name} attached — visible to the client once this milestone is complete.", "success")
    return redirect(url_for("reports.milestone_detail", slug=slug, milestone_id=milestone.id))


@reports_bp.route("/logs")
@login_required
def logs(slug):
    client = get_client_or_404(slug)
    contacts = client.contacts.order_by(ClientContact.name).all()

    q = AuditLog.query.filter_by(client_id=client.id)
    contact_id = request.args.get("contact_id", type=int)
    if contact_id:
        q = q.filter_by(client_contact_id=contact_id)
    actor = request.args.get("actor")
    if actor == "client":
        q = q.filter(AuditLog.client_contact_id.isnot(None))
    elif actor == "staff":
        q = q.filter(AuditLog.user_id.isnot(None))

    entries = q.order_by(AuditLog.created_at.desc()).limit(200).all()

    logins_30d = AuditLog.query.filter(
        AuditLog.client_id == client.id, AuditLog.action == "Signed in",
        AuditLog.created_at >= datetime.utcnow().replace(day=1),
    ).count()
    active_contacts = ClientContact.query.filter_by(client_id=client.id, portal_access=True).count()

    return render_template(
        "logs.html", client=client, active_tab="logs",
        entries=entries, contacts=contacts,
        active_contacts=active_contacts, total_contacts=len(contacts),
        logins_30d=logins_30d,
        current_contact_id=contact_id, current_actor=actor,
    )
