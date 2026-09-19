from datetime import datetime

from flask import Blueprint, render_template, request, redirect, url_for, flash, abort
from flask_login import login_required, current_user

from extensions import db
from models import Client, ActionItem, log_activity
from routes.clients import get_client_or_404

action_items_bp = Blueprint("action_items", __name__)

STATUSES = ["Not Started", "In Progress", "Waiting on Client", "Waiting on Scaalex", "Completed"]
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

    return render_template(
        "action_items_global.html", items=items, clients=clients,
        statuses=STATUSES, priorities=PRIORITIES,
        current_client_id=client_id, filters=request.args,
        active_subtab="action",
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
