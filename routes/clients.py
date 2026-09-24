import re
from datetime import datetime

import os

from flask import Blueprint, render_template, request, redirect, url_for, flash, abort, current_app
from flask_login import login_required, current_user

from extensions import db
from models import (
    Client, ClientContact, ClientAccess, Conversation, Decision, ActionItem, Milestone, MilestoneRequest,
    Document, DataRoomFolder, AIOverview, EmailDraft, FirefliesMeeting, AuditLog, log_activity,
)
from permissions import admin_required

clients_bp = Blueprint("clients", __name__, url_prefix="/clients")

# These are routes on the unified portal_hub_bp (/client-login/login, etc.) -
# a client's portal_slug can never take one of these, or it would shadow that
# route at /client-login/<slug>.
RESERVED_PORTAL_SLUGS = {"login", "choose", "logout", ""}


def slugify(name):
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    base = slug
    i = 2
    while Client.query.filter_by(slug=slug).first():
        slug = f"{base}-{i}"
        i += 1
    return slug


def get_client_or_404(slug):
    client = Client.query.filter_by(slug=slug).first()
    if not client:
        abort(404)
    if not current_user.can_view_client(client.id):
        abort(403)
    if request.method == "POST" and not current_user.can_edit_client(client.id):
        abort(403)
    return client


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


@clients_bp.route("/new", methods=["GET", "POST"])
@login_required
@admin_required
def new():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        engagement_type = request.form.get("engagement_type", "").strip() or "Advisory Engagement"
        if not name:
            flash("Client name is required.", "error")
            return redirect(url_for("clients.new"))

        client = Client(name=name, slug=slugify(name), engagement_type=engagement_type)
        db.session.add(client)
        db.session.flush()
        log_activity(current_user.id, client.id, "Client created", "client", client.id)
        db.session.commit()
        flash(f"{name} added.", "success")
        return redirect(url_for("clients.overview", slug=client.slug))

    return render_template("client_new.html")


def _response_preview(req):
    if req.response_document:
        return req.response_document.file_name
    return req.response_url or req.response_text or ""


def _build_overview_events(client, limit=40):
    """One chronological feed mixing everything that's happened on this
    engagement - milestone completions (exactly what's on the client's own
    portal), their submissions and our acceptance of them, and a short
    snippet of each conversation. Only events with a real "this happened on
    this date" carry a date here - in-progress/upcoming milestones live in
    Reports instead, since there's no natural date for "hasn't happened yet"."""
    events = []

    for m in client.milestones.filter_by(status="completed"):
        if not m.date:
            continue
        deliverable = m.staff_deliverables[0] if m.staff_deliverables else None
        detail = "Milestone completed"
        if deliverable:
            detail += f" · Deliverable: {deliverable.file_name}"
        events.append({
            "date": m.date, "kind": "Milestone", "kind_class": "badge-status-completed",
            "title": m.title, "detail": detail,
            "url": url_for("reports.milestone_detail", slug=client.slug, milestone_id=m.id),
        })

    requests_in_play = (
        MilestoneRequest.query.join(Milestone)
        .filter(Milestone.client_id == client.id, MilestoneRequest.status.in_(["fulfilled", "received", "rejected"]))
        .all()
    )
    for req in requests_in_play:
        preview = _response_preview(req)
        if req.status == "received" and req.received_at:
            events.append({
                "date": req.received_at.date(), "kind": "Received", "kind_class": "badge-status-completed",
                "title": f"{req.milestone.title} - response received", "detail": preview,
                "url": url_for("reports.milestone_detail", slug=client.slug, milestone_id=req.milestone_id),
            })
        elif req.status == "rejected" and req.rejected_at:
            events.append({
                "date": req.rejected_at.date(), "kind": "Rejected", "kind_class": "badge-overdue",
                "title": f"{req.milestone.title} - response rejected", "detail": req.rejection_comment or "",
                "url": url_for("reports.milestone_detail", slug=client.slug, milestone_id=req.milestone_id),
            })
        elif req.fulfilled_at:
            events.append({
                "date": req.fulfilled_at.date(), "kind": "Under Review", "kind_class": "badge-status-in-progress",
                "title": f"{req.milestone.title} - client responded", "detail": preview,
                "url": url_for("reports.milestone_detail", slug=client.slug, milestone_id=req.milestone_id),
            })

    for c in client.conversations.order_by(Conversation.date.desc()).limit(30):
        snippet = (c.summary or c.raw_notes or "").strip()
        if len(snippet) > 90:
            snippet = snippet[:90].rstrip() + "…"
        events.append({
            "date": c.date.date(), "kind": "Conversation", "kind_class": "badge-type",
            "title": f"{c.interaction_type} - {c.date.strftime('%d %b %Y')}",
            "detail": snippet or "No notes recorded.",
            "url": url_for("conversations.detail", slug=client.slug, conversation_id=c.id),
        })

    events.sort(key=lambda e: e["date"], reverse=True)
    return events[:limit]


@clients_bp.route("/<slug>")
@login_required
def overview(slug):
    client = get_client_or_404(slug)
    open_actions = (
        client.action_items.filter(ActionItem.status != "Completed")
        .order_by(ActionItem.due_date.is_(None), ActionItem.due_date.asc())
        .limit(6)
        .all()
    )
    recent_decisions = client.decisions.order_by(Decision.date.desc()).limit(5).all()

    needs_review = (
        MilestoneRequest.query.join(Milestone)
        .filter(Milestone.client_id == client.id, MilestoneRequest.status == "fulfilled")
        .order_by(MilestoneRequest.fulfilled_at.asc())
        .all()
    )
    events = _build_overview_events(client)

    return render_template(
        "client_overview.html",
        client=client,
        active_tab="overview",
        active_subtab="timeline",
        open_actions=open_actions,
        recent_decisions=recent_decisions,
        needs_review=needs_review,
        events=events,
    )


@clients_bp.route("/<slug>/profile/engagement", methods=["POST"])
@login_required
def update_engagement(slug):
    client = get_client_or_404(slug)
    client.status = request.form.get("status", client.status)
    client.engagement_type = request.form.get("engagement_type", client.engagement_type)
    log_activity(current_user.id, client.id, "Engagement details updated", "client", client.id)
    db.session.commit()
    flash("Engagement details updated.", "success")
    return redirect(url_for("clients.profile", slug=slug, tab="billing"))


@clients_bp.route("/<slug>/profile", methods=["GET", "POST"])
@login_required
def profile(slug):
    client = get_client_or_404(slug)
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if name:
            client.name = name
        client.registered_name = request.form.get("registered_name", "").strip() or None
        client.address = request.form.get("address", "").strip() or None
        client.website = request.form.get("website", "").strip() or None
        client.gst_number = request.form.get("gst_number", "").strip() or None
        log_activity(current_user.id, client.id, "Client profile updated", "client", client.id)
        db.session.commit()
        flash("Client profile updated.", "success")
        return redirect(url_for("clients.profile", slug=slug))

    _ensure_portal_slug(client)
    active_subtab = request.args.get("tab", "access")
    if active_subtab not in ("access", "billing"):
        active_subtab = "access"
    contacts = client.contacts.order_by(ClientContact.is_primary.desc(), ClientContact.created_at.asc()).all()
    from data_room import Tree
    tree = Tree(client.id)
    dr_visible, dr_total = tree.counts()
    dr_docs = sum(tree.direct.values())
    return render_template(
        "client_profile.html", client=client, active_tab="profile",
        contacts=contacts, active_subtab=active_subtab,
        dr_visible=dr_visible, dr_total=dr_total, dr_docs=dr_docs,
    )


@clients_bp.route("/<slug>/profile/portal-access/<int:contact_id>", methods=["POST"])
@login_required
def toggle_portal_access(slug, contact_id):
    client = get_client_or_404(slug)
    contact = ClientContact.query.filter_by(id=contact_id, client_id=client.id).first_or_404()

    turning_on = not contact.portal_access
    if turning_on:
        if not contact.email or not contact.phone:
            flash(f"{contact.name} needs both an email and a phone number on Team Members before granting portal access.", "error")
            return redirect(url_for("clients.profile", slug=slug, tab="access"))
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
    return redirect(url_for("clients.profile", slug=slug, tab="access"))


@clients_bp.route("/<slug>/profile/contacts", methods=["POST"])
@login_required
def add_contact(slug):
    client = get_client_or_404(slug)
    name = request.form.get("name", "").strip()
    if not name:
        flash("Contact name is required.", "error")
        return redirect(url_for("clients.profile", slug=slug))

    make_primary = bool(request.form.get("is_primary"))
    if make_primary:
        ClientContact.query.filter_by(client_id=client.id, is_primary=True).update({"is_primary": False})

    contact = ClientContact(
        client_id=client.id,
        name=name,
        email=request.form.get("email", "").strip() or None,
        phone=request.form.get("phone", "").strip() or None,
        designation=request.form.get("designation", "").strip() or None,
        is_primary=make_primary,
    )
    db.session.add(contact)
    db.session.flush()
    log_activity(current_user.id, client.id, "Client contact added", "client_contact", contact.id, details=name)
    db.session.commit()
    flash(f"{name} added as a contact.", "success")
    return redirect(url_for("clients.profile", slug=slug))


@clients_bp.route("/<slug>/profile/contacts/<int:contact_id>/make-primary", methods=["POST"])
@login_required
def make_primary_contact(slug, contact_id):
    client = get_client_or_404(slug)
    contact = ClientContact.query.filter_by(id=contact_id, client_id=client.id).first_or_404()
    ClientContact.query.filter_by(client_id=client.id, is_primary=True).update({"is_primary": False})
    contact.is_primary = True
    log_activity(current_user.id, client.id, "Primary contact changed", "client_contact", contact.id, details=contact.name)
    db.session.commit()
    flash(f"{contact.name} set as primary contact.", "success")
    return redirect(url_for("clients.profile", slug=slug))


@clients_bp.route("/<slug>/profile/contacts/<int:contact_id>/delete", methods=["POST"])
@login_required
def delete_contact(slug, contact_id):
    client = get_client_or_404(slug)
    contact = ClientContact.query.filter_by(id=contact_id, client_id=client.id).first_or_404()
    log_activity(current_user.id, client.id, "Client contact removed", "client_contact", contact.id, details=contact.name)
    db.session.delete(contact)
    db.session.commit()
    flash("Contact removed.", "success")
    return redirect(url_for("clients.profile", slug=slug))


def _norm_name(text):
    """Forgiving comparison for the delete confirmation: ignores case, repeated
    spaces and stray punctuation at the ends (e.g. a copied full stop)."""
    return " ".join((text or "").split()).strip(" .,;:!?\"'").lower()


@clients_bp.route("/<slug>/delete", methods=["POST"])
@login_required
def delete_client(slug):
    """Permanently remove a client and everything filed under it. Owner only,
    and the owner must type the client's name to confirm."""
    if not current_user.is_owner:
        abort(403)
    client = get_client_or_404(slug)
    if _norm_name(request.form.get("confirm_name", "")) != _norm_name(client.name):
        flash("Client not deleted: the name you typed did not match.", "error")
        return redirect(url_for("clients.profile", slug=slug))

    cid, name = client.id, client.name
    stored_names = [d.stored_name for d in Document.query.filter_by(client_id=cid).all()]
    milestone_ids = [m.id for m in Milestone.query.filter_by(client_id=cid).all()]

    # Children first so no foreign key is left dangling.
    if milestone_ids:
        MilestoneRequest.query.filter(MilestoneRequest.milestone_id.in_(milestone_ids)).delete(synchronize_session=False)
    EmailDraft.query.filter_by(client_id=cid).delete(synchronize_session=False)
    # Fireflies transcripts are the firm's own records: send them back to the
    # inbox instead of destroying them.
    FirefliesMeeting.query.filter(
        (FirefliesMeeting.assigned_client_id == cid) | (FirefliesMeeting.matched_client_id == cid)
    ).update(
        {"assigned_client_id": None, "matched_client_id": None,
         "assigned_conversation_id": None, "status": "uncategorized"},
        synchronize_session=False,
    )
    Document.query.filter_by(client_id=cid).delete(synchronize_session=False)
    DataRoomFolder.query.filter_by(client_id=cid).update({"parent_id": None}, synchronize_session=False)
    DataRoomFolder.query.filter_by(client_id=cid).delete(synchronize_session=False)
    ActionItem.query.filter_by(client_id=cid).delete(synchronize_session=False)
    Decision.query.filter_by(client_id=cid).delete(synchronize_session=False)
    AIOverview.query.filter_by(client_id=cid).delete(synchronize_session=False)
    Conversation.query.filter_by(client_id=cid).delete(synchronize_session=False)
    Milestone.query.filter_by(client_id=cid).delete(synchronize_session=False)
    AuditLog.query.filter_by(client_id=cid).delete(synchronize_session=False)
    ClientContact.query.filter_by(client_id=cid).delete(synchronize_session=False)
    ClientAccess.query.filter_by(client_id=cid).delete(synchronize_session=False)
    db.session.execute(db.text("DELETE FROM clients WHERE id = :id"), {"id": cid})
    db.session.expire_all()
    log_activity(current_user.id, None, "Client deleted", "client", cid, details=name)
    db.session.commit()

    folder = current_app.config["DOCUMENT_UPLOAD_FOLDER"]
    for stored in stored_names:
        try:
            os.remove(os.path.join(folder, stored))
        except OSError:
            pass

    flash(f"{name} and all of its data were deleted.", "success")
    return redirect(url_for("home.index"))
