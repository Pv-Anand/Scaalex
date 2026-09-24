from datetime import datetime

from flask import Blueprint, render_template, request, redirect, url_for, flash, abort, jsonify
from sqlalchemy import or_
from flask_login import login_required, current_user

from extensions import db
from models import Client, Decision, Conversation, log_activity
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

    search = request.args.get("q", "").strip()
    if search:
        like = f"%{search}%"
        q = q.filter(or_(Decision.decision.ilike(like), Decision.context.ilike(like)))

    sort = request.args.get("sort", "newest")
    q = q.order_by(Decision.date.asc() if sort == "oldest" else Decision.date.desc())
    decisions = q.all()

    needs_confirmation = [d for d in decisions if d.status == "Needs Confirmation"]
    confirmed = [d for d in decisions if d.status != "Needs Confirmation"]
    confirmed_groups = _group_by_month(confirmed)

    return render_template(
        "decisions_global.html", decisions=decisions, clients=clients,
        current_client_id=client_id, current_status=status, current_sort=sort, current_search=search,
        needs_confirmation=needs_confirmation, confirmed_groups=confirmed_groups,
        active_subtab="decisions",
    )


def _group_by_month(decisions):
    groups = []
    current_key = None
    for d in decisions:
        key = d.date.strftime("%B %Y") if d.date else "Undated"
        if key != current_key:
            groups.append({"label": key, "decisions": []})
            current_key = key
        groups[-1]["decisions"].append(d)
    return groups


@decisions_bp.route("/clients/<slug>/decisions")
@login_required
def client_list(slug):
    client = get_client_or_404(slug)
    decisions = client.decisions.order_by(Decision.date.desc()).all()
    needs_confirmation = [d for d in decisions if d.status == "Needs Confirmation"]
    confirmed = [d for d in decisions if d.status != "Needs Confirmation"]
    confirmed_groups = _group_by_month(confirmed)
    return render_template(
        "decisions_client.html", client=client, active_tab="decisions", decisions=decisions,
        needs_confirmation=needs_confirmation, confirmed_groups=confirmed_groups,
        active_subtab="decisions",
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


@decisions_bp.route("/decisions/<int:decision_id>/edit", methods=["POST"])
@login_required
def edit(decision_id):
    decision = Decision.query.get_or_404(decision_id)
    if not current_user.can_edit_client(decision.client_id):
        abort(403)

    date_str = request.form.get("date")
    if date_str:
        try:
            decision.date = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            pass

    decision.decision = request.form.get("decision", decision.decision).strip() or decision.decision
    decision.owner = request.form.get("owner", "").strip() or None
    decision.context = request.form.get("context", "").strip()
    decision.source_label = request.form.get("source_label", "").strip() or None

    log_activity(current_user.id, decision.client_id, "Decision updated", "decision", decision.id)
    db.session.commit()
    flash("Decision updated.", "success")
    return redirect(request.referrer or url_for("decisions.global_list"))


def _decision_snapshot(d):
    return {
        "client_id": d.client_id, "conversation_id": d.conversation_id, "decision": d.decision,
        "context": d.context, "owner": d.owner, "status": d.status, "source_label": d.source_label,
        "date": d.date.isoformat() if d.date else None,
        "created_at": d.created_at.isoformat() if d.created_at else None,
    }


@decisions_bp.route("/decisions/delete", methods=["POST"])
@login_required
def delete_items():
    """Delete decisions; snapshots are returned so the page can offer Undo."""
    ids = [int(i) for i in (request.get_json(silent=True) or {}).get("ids", []) if str(i).isdigit()]
    items = Decision.query.filter(Decision.id.in_(ids)).all() if ids else []
    if not items:
        return jsonify(ok=False, error="Nothing to delete."), 404
    allowed = [d for d in items if current_user.can_edit_client(d.client_id)]
    if not allowed:
        return jsonify(ok=False, error="You do not have edit access to these decisions."), 403
    snapshots = []
    for d in allowed:
        snapshots.append(_decision_snapshot(d))
        log_activity(current_user.id, d.client_id, "Decision deleted", "decision", d.id, details=(d.decision or "")[:200])
        db.session.delete(d)
    db.session.commit()
    return jsonify(ok=True, deleted=len(allowed), skipped=len(items) - len(allowed), snapshots=snapshots)


@decisions_bp.route("/decisions/restore", methods=["POST"])
@login_required
def restore_items():
    snapshots = (request.get_json(silent=True) or {}).get("snapshots") or []
    restored = 0
    for snap in snapshots[:200]:
        try:
            client_id = int(snap["client_id"])
        except (KeyError, TypeError, ValueError):
            continue
        if not Client.query.get(client_id) or not current_user.can_edit_client(client_id):
            continue
        conv_id = snap.get("conversation_id")
        if conv_id and not Conversation.query.filter_by(id=conv_id, client_id=client_id).first():
            conv_id = None

        def _dt(value):
            try:
                return datetime.fromisoformat(value) if value else datetime.utcnow()
            except ValueError:
                return datetime.utcnow()

        d = Decision(
            client_id=client_id, conversation_id=conv_id,
            decision=str(snap.get("decision") or "")[:500] or "Restored decision",
            context=snap.get("context"), owner=snap.get("owner"), status=snap.get("status") or "Confirmed",
            source_label=snap.get("source_label"), date=_dt(snap.get("date")), created_at=_dt(snap.get("created_at")),
        )
        db.session.add(d)
        db.session.flush()
        log_activity(current_user.id, client_id, "Decision restored", "decision", d.id, details=d.decision[:200])
        restored += 1
    db.session.commit()
    return jsonify(ok=True, restored=restored)
