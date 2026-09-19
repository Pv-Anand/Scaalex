"""Calendar / Activity Board - a cross-client month view over everything
with a date: conversations held, action item due dates, decisions made,
and milestone deadlines. Read-only aggregation over existing models, no
new tables."""
import calendar as calendar_module
from datetime import date, datetime, timedelta

from flask import Blueprint, render_template, request, url_for
from flask_login import login_required, current_user

from models import Client, Conversation, ActionItem, Decision, Milestone

calendar_board_bp = Blueprint("calendar_board", __name__)


def _accessible_clients():
    clients = Client.query.order_by(Client.name).all()
    if current_user.is_admin:
        return clients
    return [c for c in clients if current_user.can_view_client(c.id)]


@calendar_board_bp.route("/calendar")
@login_required
def board():
    today = date.today()
    year = request.args.get("year", type=int) or today.year
    month = request.args.get("month", type=int) or today.month
    if month < 1 or month > 12:
        year, month = today.year, today.month

    client_id = request.args.get("client_id", type=int)
    selected_str = request.args.get("date")
    try:
        selected = datetime.strptime(selected_str, "%Y-%m-%d").date() if selected_str else today
    except ValueError:
        selected = today

    clients = _accessible_clients()
    client_map = {c.id: c for c in clients}
    accessible_ids = set(client_map.keys())

    if client_id and client_id in accessible_ids:
        filter_ids = {client_id}
    else:
        client_id = None
        filter_ids = accessible_ids

    cal = calendar_module.Calendar(firstweekday=6)  # Sunday-start weeks
    month_days = list(cal.itermonthdates(year, month))
    range_start, range_end = month_days[0], month_days[-1]

    activities_by_day = {d: [] for d in month_days}

    def add(day, item):
        if day in activities_by_day:
            activities_by_day[day].append(item)

    if filter_ids:
        conversations = Conversation.query.filter(
            Conversation.client_id.in_(filter_ids),
            Conversation.date >= datetime.combine(range_start, datetime.min.time()),
            Conversation.date <= datetime.combine(range_end, datetime.max.time()),
        ).all()
        for c in conversations:
            client = client_map[c.client_id]
            add(c.date.date(), {
                "type": "meeting", "dot": "dot-meeting",
                "title": c.interaction_type or "Meeting",
                "client": client,
                "owner": c.created_by.name if c.created_by else None,
                "meta": None, "meta_danger": False,
                "url": url_for("conversations.detail", slug=client.slug, conversation_id=c.id),
                "sort": c.date,
            })

        action_items = ActionItem.query.filter(
            ActionItem.client_id.in_(filter_ids),
            ActionItem.due_date.isnot(None),
            ActionItem.due_date >= range_start,
            ActionItem.due_date <= range_end,
        ).all()
        for a in action_items:
            client = client_map[a.client_id]
            add(a.due_date, {
                "type": "action", "dot": "dot-action overdue" if a.is_overdue else "dot-action",
                "title": a.task,
                "client": client,
                "owner": a.assignee or a.owner,
                "meta": "Overdue" if a.is_overdue else a.status, "meta_danger": a.is_overdue,
                "url": url_for("action_items.client_list", slug=client.slug),
                "sort": datetime.combine(a.due_date, datetime.min.time()),
            })

        decisions = Decision.query.filter(
            Decision.client_id.in_(filter_ids),
            Decision.date >= datetime.combine(range_start, datetime.min.time()),
            Decision.date <= datetime.combine(range_end, datetime.max.time()),
        ).all()
        for dec in decisions:
            client = client_map[dec.client_id]
            add(dec.date.date(), {
                "type": "decision", "dot": "dot-decision",
                "title": dec.decision,
                "client": client,
                "owner": dec.owner,
                "meta": "Decision", "meta_danger": False,
                "url": url_for("decisions.client_list", slug=client.slug),
                "sort": dec.date,
            })

        milestones = Milestone.query.filter(Milestone.client_id.in_(filter_ids)).all()
        for m in milestones:
            d = m.display_date
            if not d or d < range_start or d > range_end:
                continue
            client = client_map[m.client_id]
            add(d, {
                "type": "milestone", "dot": "dot-milestone",
                "title": f"{m.title} — {'completed' if m.status == 'completed' else 'due'}",
                "client": client,
                "owner": m.reporting_manager.name if m.reporting_manager else None,
                "meta": m.status.replace("_", " ").title(), "meta_danger": False,
                "url": url_for("reports.milestone_detail", slug=client.slug, milestone_id=m.id),
                "sort": datetime.combine(d, datetime.min.time()),
            })

    for day_items in activities_by_day.values():
        day_items.sort(key=lambda i: (0 if i.get("meta_danger") else 1, i["sort"]))

    week_start = today - timedelta(days=(today.weekday() + 1) % 7)  # back up to Sunday
    week_end = week_start + timedelta(days=6)
    week_counts = {"meeting": 0, "action": 0, "milestone": 0, "decision": 0}
    for d, items in activities_by_day.items():
        if week_start <= d <= week_end:
            for it in items:
                week_counts[it["type"]] += 1

    selected_items = activities_by_day.get(selected, [])
    selected_clients = {it["client"].id for it in selected_items}

    prev_month, prev_year = (12, year - 1) if month == 1 else (month - 1, year)
    next_month, next_year = (1, year + 1) if month == 12 else (month + 1, year)
    weeks = [month_days[i:i + 7] for i in range(0, len(month_days), 7)]

    return render_template(
        "calendar_board.html",
        clients=clients, current_client_id=client_id,
        year=year, month=month, month_label=date(year, month, 1).strftime("%B %Y"),
        prev_year=prev_year, prev_month=prev_month,
        next_year=next_year, next_month=next_month,
        weeks=weeks, today=today, selected=selected,
        activities_by_day=activities_by_day,
        selected_items=selected_items, selected_client_count=len(selected_clients),
        week_counts=week_counts,
    )
