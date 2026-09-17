import re

from flask import Blueprint, render_template, request, redirect, url_for, flash, abort
from flask_login import login_required, current_user

from extensions import db
from models import Client, ClientContact, Conversation, Decision, ActionItem, log_activity

clients_bp = Blueprint("clients", __name__, url_prefix="/clients")


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
    return client


@clients_bp.route("/new", methods=["GET", "POST"])
@login_required
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


@clients_bp.route("/<slug>")
@login_required
def overview(slug):
    client = get_client_or_404(slug)
    recent_conversations = client.conversations.order_by(Conversation.date.desc()).limit(5).all()
    open_actions = (
        client.action_items.filter(ActionItem.status != "Completed")
        .order_by(ActionItem.due_date.is_(None), ActionItem.due_date.asc())
        .limit(6)
        .all()
    )
    recent_decisions = client.decisions.order_by(Decision.date.desc()).limit(5).all()

    return render_template(
        "client_overview.html",
        client=client,
        active_tab="overview",
        recent_conversations=recent_conversations,
        open_actions=open_actions,
        recent_decisions=recent_decisions,
    )


@clients_bp.route("/<slug>/settings", methods=["GET", "POST"])
@login_required
def settings(slug):
    client = get_client_or_404(slug)
    if request.method == "POST":
        client.status = request.form.get("status", client.status)
        client.engagement_type = request.form.get("engagement_type", client.engagement_type)
        log_activity(current_user.id, client.id, "Client settings updated", "client", client.id)
        db.session.commit()
        flash("Client settings updated.", "success")
        return redirect(url_for("clients.settings", slug=slug))

    return render_template("client_settings.html", client=client, active_tab="settings")


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

    contacts = client.contacts.order_by(ClientContact.is_primary.desc(), ClientContact.created_at.asc()).all()
    return render_template("client_profile.html", client=client, active_tab="profile", contacts=contacts)


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
