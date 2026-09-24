from datetime import datetime

from flask import Blueprint, render_template, request, redirect, url_for, flash, abort, jsonify
from flask_login import login_required, current_user

from brand import BRAND
from extensions import db
from models import Client, ActionItem, Conversation, log_activity
from routes.clients import get_client_or_404

action_items_bp = Blueprint("action_items", __name__)

STATUSES = ["Not Started", "In Progress", "Waiting on Client", f"Waiting on {BRAND.name}", "Completed"]
PRIORITIES = ["High", "Medium", "Low"]


def _apply_filters_and_sort(q, args):
    status = args.get("status")
    if status:
        q = q.filter(ActionItem.status == status)
    priority = args.get("priority")
    if priority:
        q = q.filter(ActionItem.priority == priority)
    owner = args.get("owner", "").strip()
    if owner:
        q = q.filter(ActionItem.owner.ilike(f"%{owner}%"))
    assignee = args.get("assignee", "").strip()
    if assignee:
        q = q.filter(ActionItem.assignee.ilike(f"%{assignee}%"))

    sort = args.get("sort", "due_date")
    if sort == "priority":
        order_map = {"High": 0, "Medium": 1, "Low": 2}
        items = q.all()
        items.sort(key=lambda i: order_map.get(i.priority, 3))
        return items
    items = q.order_by(ActionItem.due_date.is_(None), ActionItem.due_date.asc()).all()
    return items


@action_items_bp.route("/action-items")
@login_required
def global_list():
    clients = Client.query.order_by(Client.name).all()
    if not current_user.is_admin:
        clients = [c for c in clients if current_user.can_view_client(c.id)]
    accessible_ids = [c.id for c in clients]

    q = ActionItem.query.filter(ActionItem.client_id.in_(accessible_ids))
    client_id = request.args.get("client_id", type=int)
    if client_id and client_id in accessible_ids:
        q = q.filter_by(client_id=client_id)
    else:
        client_id = None

    items = _apply_filters_and_sort(q, request.args)

    people = set()
    for a in items:
        for name in (a.owner, a.assignee):
            if name and name != "Unassigned":
                people.add(name)

    return render_template(
        "action_items_global.html", items=items, clients=clients,
        statuses=STATUSES, priorities=PRIORITIES,
        current_client_id=client_id, filters=request.args,
        people=sorted(people), active_subtab="action",
    )


@action_items_bp.route("/clients/<slug>/action-items")
@login_required
def client_list(slug):
    client = get_client_or_404(slug)
    q = client.action_items
    items = _apply_filters_and_sort(q, request.args)

    people = set()
    for a in client.action_items:
        for name in (a.owner, a.assignee):
            if name and name != "Unassigned":
                people.add(name)

    return render_template(
        "action_items_client.html", client=client, active_tab="action_items",
        items=items, statuses=STATUSES, priorities=PRIORITIES, filters=request.args,
        people=sorted(people), active_subtab="action",
    )


@action_items_bp.route("/clients/<slug>/action-items/new", methods=["GET", "POST"])
@login_required
def new(slug):
    client = get_client_or_404(slug)

    if request.method == "POST":
        due_date = None
        due_str = request.form.get("due_date")
        if due_str:
            try:
                due_date = datetime.strptime(due_str, "%Y-%m-%d").date()
            except ValueError:
                due_date = None

        item = ActionItem(
            client_id=client.id,
            task=request.form.get("task", "").strip(),
            owner=request.form.get("owner", "").strip() or "Unassigned",
            assignee=request.form.get("assignee", "").strip() or None,
            due_date=due_date,
            priority=request.form.get("priority", "Medium"),
            status=request.form.get("status", "Not Started"),
            notes=request.form.get("notes", "").strip() or None,
            source_label=request.form.get("source_label", "").strip() or "Manually added",
        )
        db.session.add(item)
        db.session.flush()
        log_activity(current_user.id, client.id, "Action item created", "action_item", item.id)
        db.session.commit()
        flash("Action item added.", "success")
        return redirect(url_for("action_items.client_list", slug=slug))

    return render_template(
        "action_item_new.html", client=client, active_tab="action_items",
        statuses=STATUSES, priorities=PRIORITIES,
    )


@action_items_bp.route("/action-items/<int:item_id>/status", methods=["POST"])
@login_required
def update_status(item_id):
    item = ActionItem.query.get_or_404(item_id)
    if not current_user.can_edit_client(item.client_id):
        abort(403)
    new_status = request.form.get("status", item.status)
    item.status = new_status
    action = "Action item completed" if new_status == "Completed" else "Action item status updated"
    log_activity(current_user.id, item.client_id, action, "action_item", item.id, details=new_status)
    db.session.commit()
    if request.headers.get("X-Requested-With") == "fetch":
        return {"ok": True, "status": new_status}
    flash("Action item updated.", "success")
    return redirect(request.referrer or url_for("action_items.global_list"))


@action_items_bp.route("/action-items/<int:item_id>/edit", methods=["POST"])
@login_required
def edit(item_id):
    item = ActionItem.query.get_or_404(item_id)
    if not current_user.can_edit_client(item.client_id):
        abort(403)

    due_date = None
    due_str = request.form.get("due_date")
    if due_str:
        try:
            due_date = datetime.strptime(due_str, "%Y-%m-%d").date()
        except ValueError:
            due_date = None

    item.task = request.form.get("task", "").strip() or item.task
    item.assignee = request.form.get("assignee", "").strip() or None
    item.owner = request.form.get("owner", "").strip() or "Unassigned"
    item.priority = request.form.get("priority", item.priority)
    item.due_date = due_date
    item.status = request.form.get("status", item.status)
    item.notes = request.form.get("notes", "").strip() or None

    log_activity(current_user.id, item.client_id, "Action item updated", "action_item", item.id)
    db.session.commit()
    flash("Action item updated.", "success")
    return redirect(request.referrer or url_for("action_items.global_list"))


def _item_snapshot(a):
    return {
        "client_id": a.client_id, "conversation_id": a.conversation_id, "task": a.task, "owner": a.owner,
        "assignee": a.assignee, "due_date": a.due_date.isoformat() if a.due_date else None,
        "priority": a.priority, "status": a.status, "notes": a.notes, "source_label": a.source_label,
        "needs_confirmation": bool(a.needs_confirmation),
        "created_at": a.created_at.isoformat() if a.created_at else None,
    }


@action_items_bp.route("/action-items/delete", methods=["POST"])
@login_required
def delete_items():
    """Delete one or more action items. Returns snapshots so the page can offer
    Undo (see restore_items); the delete itself is real and immediate."""
    ids = [int(i) for i in (request.get_json(silent=True) or {}).get("ids", []) if str(i).isdigit()]
    items = ActionItem.query.filter(ActionItem.id.in_(ids)).all() if ids else []
    if not items:
        return jsonify(ok=False, error="Nothing to delete."), 404
    allowed = [a for a in items if current_user.can_edit_client(a.client_id)]
    if not allowed:
        return jsonify(ok=False, error="You do not have edit access to these tasks."), 403
    snapshots = []
    for a in allowed:
        snapshots.append(_item_snapshot(a))
        log_activity(current_user.id, a.client_id, "Action item deleted", "action_item", a.id, details=(a.task or "")[:200])
        db.session.delete(a)
    db.session.commit()
    return jsonify(ok=True, deleted=len(allowed), skipped=len(items) - len(allowed), snapshots=snapshots)


@action_items_bp.route("/action-items/restore", methods=["POST"])
@login_required
def restore_items():
    """Undo a delete: recreate the items from the snapshots delete_items returned."""
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
        try:
            due = datetime.strptime(snap["due_date"], "%Y-%m-%d").date() if snap.get("due_date") else None
        except ValueError:
            due = None
        try:
            created = datetime.fromisoformat(snap["created_at"]) if snap.get("created_at") else datetime.utcnow()
        except ValueError:
            created = datetime.utcnow()
        item = ActionItem(
            client_id=client_id, conversation_id=conv_id, task=str(snap.get("task") or "")[:500] or "Restored task",
            owner=snap.get("owner"), assignee=snap.get("assignee"), due_date=due,
            priority=snap.get("priority") or "Medium", status=snap.get("status") or "Not Started",
            notes=snap.get("notes"), source_label=snap.get("source_label"),
            needs_confirmation=bool(snap.get("needs_confirmation")), created_at=created,
        )
        db.session.add(item)
        db.session.flush()
        log_activity(current_user.id, client_id, "Action item restored", "action_item", item.id, details=item.task[:200])
        restored += 1
    db.session.commit()
    return jsonify(ok=True, restored=restored)
