from datetime import datetime

from flask import Blueprint, render_template, request, redirect, url_for, flash, abort
from flask_login import login_required, current_user

from extensions import db
from models import Client, Decision, log_activity
from routes.clients import get_client_or_404

decisions_bp = Blueprint("decisions", __name__)


@decisions_bp.route("/decisions")
@login_required
def global_list():
    clients = Client.query.order_by(Client.name).all()
    if not current_user.is_admin:
        clients = [c for c in clients if current_user.can_view_client(c.id)]
    accessible_ids = [c.id for c in clients]

    q = Decision.query.filter(Decision.client_id.in_(accessible_ids))
    client_id = request.args.get("client_id", type=int)
    if client_id and client_id in accessible_ids:
        q = q.filter_by(client_id=client_id)
    else:
        client_id = None
    status = request.args.get("status")
    if status:
        q = q.filter_by(status=status)

    decisions = q.order_by(Decision.date.desc()).all()
    return render_template(
        "decisions_global.html", decisions=decisions, clients=clients,
        current_client_id=client_id, current_status=status,
    )


@decisions_bp.route("/clients/<slug>/decisions")
@login_required
def client_list(slug):
    client = get_client_or_404(slug)
    decisions = client.decisions.order_by(Decision.date.desc()).all()
    return render_template(
        "decisions_client.html", client=client, active_tab="decisions", decisions=decisions,
    )


@decisions_bp.route("/clients/<slug>/decisions/new", methods=["GET", "POST"])
@login_required
def new(slug):
    client = get_client_or_404(slug)

    if request.method == "POST":
        date_str = request.form.get("date")
        try:
            date = datetime.strptime(date_str, "%Y-%m-%d") if date_str else datetime.utcnow()
        except ValueError:
            date = datetime.utcnow()

        decision = Decision(
            client_id=client.id,
            decision=request.form.get("decision", "").strip(),
            context=request.form.get("context", "").strip(),
            owner=request.form.get("owner", "").strip() or None,
            status=request.form.get("status", "Confirmed"),
            source_label=request.form.get("source_label", "").strip() or "Manually recorded",
            date=date,
        )
        db.session.add(decision)
        db.session.flush()
        log_activity(current_user.id, client.id, "Decision recorded", "decision", decision.id)
        db.session.commit()
        flash("Decision recorded.", "success")
        return redirect(url_for("decisions.client_list", slug=slug))

    return render_template("decision_new.html", client=client, active_tab="decisions")


@decisions_bp.route("/decisions/<int:decision_id>/status", methods=["POST"])
@login_required
def update_status(decision_id):
    decision = Decision.query.get_or_404(decision_id)
    if not current_user.can_edit_client(decision.client_id):
        abort(403)
    decision.status = request.form.get("status", decision.status)
    log_activity(current_user.id, decision.client_id, "Decision status updated", "decision", decision.id)
    db.session.commit()
    flash("Decision updated.", "success")
    return redirect(request.referrer or url_for("decisions.global_list"))
