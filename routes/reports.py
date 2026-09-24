"""Reports & Logs tabs - the internal side of the client portal.

Reports controls the milestone timeline that drives a client's portal view;
Logs shows what actually happened - both client-side portal activity and the
staff actions that affect it - in one merged feed via AuditLog. Portal access
per contact lives on the Client Profile page (routes/clients.py) since that's
also where team members are managed.
"""
import os
import uuid
from datetime import datetime, timedelta

from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename

from extensions import db
from models import (
    ClientContact, Milestone, MilestoneRequest, Document, User, AuditLog,
    log_activity,
)
from routes.clients import get_client_or_404, _ensure_portal_slug
from routes.documents import _allowed

reports_bp = Blueprint("reports", __name__, url_prefix="/clients/<slug>")

MILESTONE_STATUSES = ["upcoming", "in_progress", "completed"]
REQUEST_TYPES = [("data", "Data"), ("url", "URL"), ("document", "Document")]


@reports_bp.route("/reports")
@login_required
def reports(slug):
    client = get_client_or_404(slug)
    _ensure_portal_slug(client)

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
        milestones=milestones,
    )


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

        request_message = request.form.get("request_message", "").strip()
        if request_message:
            request_type = request.form.get("request_type", "data")
            if request_type not in dict(REQUEST_TYPES):
                request_type = "data"
            req = MilestoneRequest(
                milestone_id=milestone.id, request_type=request_type, message=request_message,
                requested_by_id=current_user.id,
            )
            db.session.add(req)
            db.session.flush()
            log_activity(current_user.id, client.id, "Requested from client", "milestone_request", req.id, details=f"{title}: {request_message}")

        deliverable = request.files.get("deliverable_file")
        if deliverable and deliverable.filename and _allowed(deliverable.filename):
            original_name = secure_filename(deliverable.filename)
            ext = original_name.rsplit(".", 1)[-1].lower()
            stored_name = f"{uuid.uuid4().hex}.{ext}"
            path = os.path.join(current_app.config["DOCUMENT_UPLOAD_FOLDER"], stored_name)
            deliverable.save(path)
            doc = Document(
                client_id=client.id, milestone_id=milestone.id,
                file_name=original_name, stored_name=stored_name, file_type=ext,
                file_size=os.path.getsize(path), uploaded_by_id=current_user.id,
            )
            db.session.add(doc)
            db.session.flush()
            log_activity(current_user.id, client.id, "Deliverable attached", "document", doc.id, details=f"{title}: {original_name}")

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
    deliverables = milestone.staff_deliverables
    past_requests = (
        milestone.requests.filter(MilestoneRequest.status.in_(["fulfilled", "received", "rejected"])).all()
    )
    return render_template(
        "milestone_detail.html", client=client, active_tab="reports",
        milestone=milestone, managers=managers, deliverables=deliverables,
        past_requests=past_requests,
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


@reports_bp.route("/reports/milestones/<int:milestone_id>/delete", methods=["POST"])
@login_required
def delete_milestone(slug, milestone_id):
    client = get_client_or_404(slug)
    milestone = Milestone.query.filter_by(id=milestone_id, client_id=client.id).first_or_404()
    title = milestone.title

    # Keep uploaded files on record rather than deleting them - just unlink
    # them from the milestone that's going away.
    Document.query.filter_by(milestone_id=milestone.id).update({"milestone_id": None})

    log_activity(current_user.id, client.id, "Milestone deleted", "milestone", milestone.id, details=title)
    db.session.delete(milestone)
    db.session.commit()
    flash(f'"{title}" deleted.', "success")
    return redirect(url_for("reports.reports", slug=slug))


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
    flash("Request sent - it now shows on the client's portal.", "success")
    return redirect(url_for("reports.milestone_detail", slug=slug, milestone_id=milestone.id))


@reports_bp.route("/reports/requests/<int:request_id>/accept", methods=["POST"])
@login_required
def accept_request(slug, request_id):
    client = get_client_or_404(slug)
    req = MilestoneRequest.query.join(Milestone).filter(
        MilestoneRequest.id == request_id, Milestone.client_id == client.id,
    ).first_or_404()

    if req.status != "fulfilled":
        flash("This request isn't awaiting acceptance.", "error")
        return redirect(request.referrer or url_for("clients.overview", slug=slug))

    req.status = "received"
    req.received_by_id = current_user.id
    req.received_at = datetime.utcnow()
    log_activity(
        current_user.id, client.id, "Client submission accepted", "milestone_request", req.id,
        details=req.milestone.title,
    )
    db.session.commit()
    flash("Marked as received.", "success")
    return redirect(request.referrer or url_for("clients.overview", slug=slug))


@reports_bp.route("/reports/requests/<int:request_id>/reject", methods=["POST"])
@login_required
def reject_request(slug, request_id):
    client = get_client_or_404(slug)
    req = MilestoneRequest.query.join(Milestone).filter(
        MilestoneRequest.id == request_id, Milestone.client_id == client.id,
    ).first_or_404()

    if req.status != "fulfilled":
        flash("This request isn't awaiting review.", "error")
        return redirect(request.referrer or url_for("clients.overview", slug=slug))

    comment = request.form.get("comment", "").strip()
    if not comment:
        flash("Add a comment explaining what's needed, so the client knows what to fix.", "error")
        return redirect(request.referrer or url_for("clients.overview", slug=slug))

    req.status = "rejected"
    req.rejection_comment = comment
    req.rejected_by_id = current_user.id
    req.rejected_at = datetime.utcnow()

    # Reopen the same ask (unless one's already open again somehow) so it
    # reappears as an action item for the client, now that they know what
    # needs to change - rather than staff having to re-send it by hand.
    # req.status is already "rejected" above, so active_request (which only
    # matches "awaiting") can't be this same row.
    if not req.milestone.active_request:
        reopened = MilestoneRequest(
            milestone_id=req.milestone_id, request_type=req.request_type, message=req.message,
            requested_by_id=current_user.id,
        )
        db.session.add(reopened)

    log_activity(
        current_user.id, client.id, "Client submission rejected", "milestone_request", req.id,
        details=f"{req.milestone.title}: {comment}",
    )
    db.session.commit()
    flash("Rejected - the client will see your comment and can resubmit.", "success")
    return redirect(request.referrer or url_for("clients.overview", slug=slug))


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
    flash(f"{original_name} attached - visible to the client once this milestone is complete.", "success")
    return redirect(url_for("reports.milestone_detail", slug=slug, milestone_id=milestone.id))


ACCESS_ACTIONS = {
    "Portal access granted", "Portal access revoked", "Password set",
    "Primary contact changed", "Client contact added", "Client contact removed",
    "Terms accepted", "Data Room access granted", "Data Room access revoked",
    "Data Room turned on", "Data Room turned off",
}


def _annotate_log_entry(entry, client):
    """Attach a category, a legend dot class, and a "View" link to a raw
    AuditLog row, using the entity_type/entity_id it already stores but the
    old flat table never surfaced."""
    if entry.action in ACCESS_ACTIONS:
        entry.log_category = "access"
        entry.dot_class = "dot-log-access"
        entry.view_url = url_for("clients.profile", slug=client.slug)
        entry.view_label = "View access"
    elif entry.action == "Signed in":
        entry.log_category = "signin"
        entry.dot_class = "dot-log-signin"
        entry.view_url = None
        entry.view_label = None
    elif entry.entity_type == "document":
        entry.log_category = "document"
        entry.dot_class = "dot-log-document"
        entry.view_url = url_for("documents.client_list", slug=client.slug)
        entry.view_label = "View document"
    elif entry.entity_type == "data_room_folder":
        entry.log_category = "document"
        entry.dot_class = "dot-log-document"
        entry.view_url = url_for("data_room.page", slug=client.slug, folder=entry.entity_id) if entry.entity_id else url_for("data_room.page", slug=client.slug)
        entry.view_label = "View Data Room"
    elif entry.entity_type == "decision":
        entry.log_category = "decision"
        entry.dot_class = "dot-decision"
        entry.view_url = url_for("decisions.client_list", slug=client.slug)
        entry.view_label = "View decision"
    elif entry.entity_type == "action_item":
        entry.log_category = "action_item"
        entry.dot_class = "dot-action"
        entry.view_url = url_for("action_items.client_list", slug=client.slug)
        entry.view_label = "View action item"
    elif entry.entity_type == "milestone":
        entry.log_category = "milestone"
        entry.dot_class = "dot-milestone"
        entry.view_url = (
            url_for("reports.milestone_detail", slug=client.slug, milestone_id=entry.entity_id)
            if entry.entity_id else None
        )
        entry.view_label = "View milestone"
    elif entry.entity_type == "milestone_request":
        req = MilestoneRequest.query.get(entry.entity_id) if entry.entity_id else None
        entry.log_category = "milestone"
        entry.dot_class = "dot-milestone"
        entry.view_url = (
            url_for("reports.milestone_detail", slug=client.slug, milestone_id=req.milestone_id)
            if req else None
        )
        entry.view_label = "View milestone"
    else:
        entry.log_category = "other"
        entry.dot_class = "dot-log-other"
        entry.view_url = None
        entry.view_label = None
    return entry


def _group_log_entries_by_day(entries):
    groups = []
    current_key = None
    for e in entries:
        key = e.created_at.strftime("%A, %d %b %Y")
        if key != current_key:
            groups.append({"label": key, "entries": []})
            current_key = key
        groups[-1]["entries"].append(e)
    return groups


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
    for e in entries:
        _annotate_log_entry(e, client)

    category = request.args.get("category")
    if category:
        entries = [e for e in entries if e.log_category == category]

    entry_groups = _group_log_entries_by_day(entries)

    thirty_days_ago = datetime.utcnow() - timedelta(days=30)
    logins_30d = AuditLog.query.filter(
        AuditLog.client_id == client.id, AuditLog.action == "Signed in",
        AuditLog.created_at >= datetime.utcnow().replace(day=1),
    ).count()
    documents_30d = AuditLog.query.filter(
        AuditLog.client_id == client.id, AuditLog.entity_type == "document",
        AuditLog.created_at >= thirty_days_ago,
    ).count()
    access_changes_30d = AuditLog.query.filter(
        AuditLog.client_id == client.id, AuditLog.action.in_(ACCESS_ACTIONS),
        AuditLog.created_at >= thirty_days_ago,
    ).count()
    active_contacts = ClientContact.query.filter_by(client_id=client.id, portal_access=True).count()

    return render_template(
        "logs.html", client=client, active_tab="logs",
        entry_groups=entry_groups, contacts=contacts,
        active_contacts=active_contacts, total_contacts=len(contacts),
        logins_30d=logins_30d, documents_30d=documents_30d, access_changes_30d=access_changes_30d,
        current_contact_id=contact_id, current_actor=actor, current_category=category,
    )
