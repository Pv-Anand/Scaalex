"""Calendar / Activity Board - a cross-client rolling day-strip view over
everything with a date: conversations held, action item due dates,
decisions made, and milestone deadlines. Read-only aggregation over
existing models, no new tables."""
from datetime import date, datetime, timedelta

from flask import Blueprint, render_template, request, url_for
from flask_login import login_required, current_user

from models import Client, Conversation, ActionItem, Decision, Milestone

calendar_board_bp = Blueprint("calendar_board", __name__)

WINDOW_DAYS = 5


def _accessible_clients():
    clients = Client.query.order_by(Client.name).all()
    if current_user.is_admin:
        return clients
    return [c for c in clients if current_user.can_view_client(c.id)]


@calendar_board_bp.route("/calendar")
@login_required
def board():
    today = date.today()
    start_str = request.args.get("start")
    try:
        window_start = datetime.strptime(start_str, "%Y-%m-%d").date() if start_str else today
    except ValueError:
        window_start = today

    days = [window_start + timedelta(days=i) for i in range(WINDOW_DAYS)]
    window_end = days[-1]
    prev_start = window_start - timedelta(days=WINDOW_DAYS)
    next_start = window_start + timedelta(days=WINDOW_DAYS)

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

    # "This Week" (Sun-Sat around today) is always reported regardless of
    # which days are currently in view, so the query range covers the union
    # of the visible window and that week.
    week_start = today - timedelta(days=(today.weekday() + 1) % 7)  # back up to Sunday
    week_end = week_start + timedelta(days=6)
    range_start = min(window_start, week_start)
    range_end = max(window_end, week_end)
    all_days = [range_start + timedelta(days=i) for i in range((range_end - range_start).days + 1)]

    activities_by_day = {d: [] for d in all_days}

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
            ActionItem.status != "Completed",
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

        milestones = Milestone.query.filter(
            Milestone.client_id.in_(filter_ids),
            Milestone.status != "completed",
        ).all()
        for m in milestones:
            d = m.due_date
            if not d or d < range_start or d > range_end:
                continue
            client = client_map[m.client_id]
            add(d, {
                "type": "milestone", "dot": "dot-milestone",
                "title": f"{m.title} — due",
                "client": client,
                "owner": m.reporting_manager.name if m.reporting_manager else None,
                "meta": m.status.replace("_", " ").title(), "meta_danger": False,
                "url": url_for("reports.milestone_detail", slug=client.slug, milestone_id=m.id),
                "sort": datetime.combine(d, datetime.min.time()),
            })

    for day_items in activities_by_day.values():
        day_items.sort(key=lambda i: (0 if i.get("meta_danger") else 1, i["sort"]))

    clients_by_day = {}
    for d, items in activities_by_day.items():
        counts = {}
        order = []
        for it in items:
            c = it["client"]
            if c.id not in counts:
                counts[c.id] = 0
                order.append(c)
            counts[c.id] += 1
        clients_by_day[d] = [(c, counts[c.id]) for c in order]

    # Per-day counts by type (plus a separate overdue bucket), used to render
    # composition dots on each cell instead of a bare client/count chip.
    type_summary_by_day = {}
    for d, items in activities_by_day.items():
        counts = {"meeting": 0, "action": 0, "overdue": 0, "decision": 0, "milestone": 0}
        for it in items:
            if it["type"] == "action" and it.get("meta_danger"):
                counts["overdue"] += 1
            else:
                counts[it["type"]] += 1
        type_summary_by_day[d] = counts

    week_counts = {"meeting": 0, "action": 0, "milestone": 0, "decision": 0}
    for d, items in activities_by_day.items():
        if week_start <= d <= week_end:
            for it in items:
                week_counts[it["type"]] += 1

    selected_items = activities_by_day.get(selected, [])
    selected_clients = {it["client"].id for it in selected_items}

    if window_start.year == window_end.year and window_start.month == window_end.month:
        window_label = f"{window_start.day} – {window_end.strftime('%d %b %Y')}"
    elif window_start.year == window_end.year:
        window_label = f"{window_start.strftime('%d %b')} – {window_end.strftime('%d %b %Y')}"
    else:
        window_label = f"{window_start.strftime('%d %b %Y')} – {window_end.strftime('%d %b %Y')}"

    return render_template(
        "calendar_board.html",
        clients=clients, current_client_id=client_id,
        window_start=window_start, window_label=window_label,
        prev_start=prev_start, next_start=next_start,
        days=days, today=today, selected=selected,
        activities_by_day=activities_by_day, clients_by_day=clients_by_day,
        type_summary_by_day=type_summary_by_day,
        selected_items=selected_items, selected_client_count=len(selected_clients),
        week_counts=week_counts,
    )
