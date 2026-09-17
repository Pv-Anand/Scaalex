import re

from flask import Blueprint, render_template, request, redirect, url_for, flash, abort
from flask_login import login_required, current_user

from extensions import db
from models import Client, Conversation, Decision, ActionItem, log_activity

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
