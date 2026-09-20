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

portal_bp = Blueprint("portal", __name__, url_prefix="/client-login/<portal_slug>")


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

    return render_template("portal_login.html", client=client, current_year=datetime.utcnow().year)


@portal_bp.route("/set-password", methods=["GET", "POST"])
@portal_login_required
def set_password(portal_slug, client, contact):
    if not contact.must_change_password:
        return redirect(url_for("portal.timeline", portal_slug=portal_slug))

    if request.method == "POST":
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")
        agree_terms = request.form.get("agree_terms")
        if len(new_password) < 8:
            flash("Password must be at least 8 characters.", "error")
        elif new_password != confirm_password:
            flash("Passwords don't match.", "error")
        elif not agree_terms:
            flash("Please agree to the Terms & Conditions and Privacy Policy to continue.", "error")
        else:
            contact.set_password(new_password)
            contact.must_change_password = False
            contact.terms_accepted_at = datetime.utcnow()
            log_portal_activity(contact.id, client.id, "Password set", "client_contact", contact.id)
            log_portal_activity(contact.id, client.id, "Terms accepted", "client_contact", contact.id)
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

    # Every open request across every visible milestone, not just the one
    # "Next Up" milestone - a request on an upcoming or already-completed
    # milestone must still reach the client, not just one sent on whichever
    # single milestone happens to be in progress right now.
    open_requests = (
        MilestoneRequest.query.join(Milestone)
        .filter(
            Milestone.client_id == client.id,
            Milestone.visible_to_client.is_(True),
            MilestoneRequest.status == "awaiting",
        )
        .order_by(MilestoneRequest.requested_at.asc())
        .all()
    )

    # What they've just sent back, so it doesn't just vanish from the page -
    # still visible until the milestone itself moves on (marked complete),
    # so the client can see it was received and is waiting on Scaalex. Stays
    # visible (with an updated badge) once a staff member accepts it too,
    # so the "Under Review" -> "Received" transition is visible, not silent.
    under_review = (
        MilestoneRequest.query.join(Milestone)
        .filter(
            Milestone.client_id == client.id,
            Milestone.visible_to_client.is_(True),
            Milestone.status != "completed",
            MilestoneRequest.status.in_(["fulfilled", "received", "rejected"]),
        )
        .order_by(MilestoneRequest.fulfilled_at.desc())
        .all()
    )

    return render_template(
        "portal_timeline.html", client=client, contact=contact,
        completed=completed, next_up=next_up, ahead=ahead,
        total_visible=total_visible, completed_count=completed_count,
        open_requests=open_requests, under_review=under_review,
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

    # One field on the client's side ("Share a Link Instead") covers both a
    # URL and free text - simpler UI than asking them to pick which kind of
    # box to fill in. Detect which it is here instead.
    raw_value = request.form.get("response_text", "").strip()
    is_url = raw_value.lower().startswith(("http://", "https://"))
    url_value = raw_value if is_url else ""
    text_value = raw_value if raw_value and not is_url else ""
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
    flash("Submitted - this is now under review by Scaalex.", "success")
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


# ---------------------------------------------------------------------------
# Unified entry point - one memorable URL for every client, instead of each
# needing their own project's link. Looks a contact up by email+password
# across ALL clients (a contact row is still scoped to one client - this
# just finds the right one instead of requiring the visitor to already know
# which project's URL to use) and either drops them straight into their one
# project or, in the rare case the same email/password combination matches
# more than one, lets them pick. Everything downstream (timeline, set-
# password, etc.) is still the same per-project portal_bp route, reached
# via its own portal_slug - this only shortcuts finding that URL.
# ---------------------------------------------------------------------------
portal_hub_bp = Blueprint("portal_hub", __name__, url_prefix="/client-login")


def _accessible_contact():
    contact = ClientContact.query.get(session.get("portal_contact_id") or 0)
    if contact and contact.portal_access and contact.client and contact.client.portal_slug:
        return contact
    return None


def _finish_hub_login(contact):
    session.pop("portal_candidate_ids", None)
    session["portal_contact_id"] = contact.id
    contact.last_login_at = datetime.utcnow()
    log_portal_activity(contact.id, contact.client_id, "Signed in", "client_contact", contact.id)
    db.session.commit()
    if contact.must_change_password:
        return redirect(url_for("portal.set_password", portal_slug=contact.client.portal_slug))
    return redirect(url_for("portal.timeline", portal_slug=contact.client.portal_slug))


@portal_hub_bp.route("/")
def hub_index():
    contact = _accessible_contact()
    if contact:
        return redirect(url_for("portal.timeline", portal_slug=contact.client.portal_slug))
    return redirect(url_for("portal_hub.login"))


@portal_hub_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute", methods=["POST"])
def login():
    contact = _accessible_contact()
    if contact:
        return redirect(url_for("portal.timeline", portal_slug=contact.client.portal_slug))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "").strip()

        candidates = ClientContact.query.filter(
            ClientContact.portal_access.is_(True),
            db.func.lower(ClientContact.email) == email,
        ).all()
        matches = [
            c for c in candidates
            if c.check_password(password) and c.client and c.client.portal_slug
        ]

        if not matches:
            flash("Invalid email or mobile number.", "error")
        elif len(matches) == 1:
            return _finish_hub_login(matches[0])
        else:
            # Same person, portal access on more than one engagement - let
            # them choose rather than guessing which one they meant.
            session["portal_candidate_ids"] = [c.id for c in matches]
            return redirect(url_for("portal_hub.choose"))

    return render_template("portal_hub_login.html", current_year=datetime.utcnow().year)


@portal_hub_bp.route("/choose", methods=["GET", "POST"])
def choose():
    ids = session.get("portal_candidate_ids") or []
    contacts = ClientContact.query.filter(ClientContact.id.in_(ids)).all() if ids else []
    contacts = [c for c in contacts if c.portal_access and c.client and c.client.portal_slug]
    if not contacts:
        return redirect(url_for("portal_hub.login"))

    if request.method == "POST":
        chosen_id = request.form.get("contact_id", type=int)
        contact = next((c for c in contacts if c.id == chosen_id), None)
        if not contact:
            flash("Choose one of your projects.", "error")
            return redirect(url_for("portal_hub.choose"))
        return _finish_hub_login(contact)

    return render_template("portal_hub_choose.html", contacts=contacts)


@portal_hub_bp.route("/logout")
def hub_logout():
    session.pop("portal_contact_id", None)
    session.pop("portal_candidate_ids", None)
    return redirect(url_for("portal_hub.login"))
