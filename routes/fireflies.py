from datetime import datetime, timedelta, timezone

from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app
from flask_login import login_required, current_user

from extensions import db, limiter
from models import AppSetting, Client, Conversation, FirefliesMeeting, CalendarConnection, log_activity
from ai.fireflies_client import (
    FirefliesConfigError, FirefliesRequestError,
    fetch_recent_transcripts, fetch_transcript_detail,
    parse_fireflies_date, extract_participant_names, build_transcript_text,
    pick_overview, pick_action_items, pick_highlights, parse_highlights, parse_action_items,
)
from ai.google_calendar_client import CalendarRequestError, fetch_upcoming_events, parse_event
from routes.calendar import get_valid_access_token

fireflies_bp = Blueprint("fireflies", __name__, url_prefix="/fireflies")


def guess_client_match(title, participant_names):
    """Substring-match the meeting title/participants against client names.

    Deliberately simple and explainable rather than AI-based: a wrong
    heuristic match just means a meeting stays "uncategorized" a bit too
    eagerly or lands under the wrong client, either of which an advisor can
    immediately see and correct - there's no room here for a confident-
    sounding but fabricated match the way an AI guess could produce.
    """
    haystacks = [(title or "").lower()] + [p.lower() for p in participant_names]
    for client in Client.query.all():
        name_lower = client.name.lower()
        if any(name_lower in h for h in haystacks):
            return client
    return None


def sales_addresses():
    """Addresses that mark a meeting as a sales meeting: the list an
    Administrator saved in Integrations, else the SALES_MEETING_EMAILS default."""
    row = AppSetting.query.get("sales_meeting_emails")
    if row and (row.value or "").strip():
        return [a.strip().lower() for a in row.value.split(",") if a.strip()]
    return list(current_app.config.get("SALES_MEETING_EMAILS") or ())


def _is_sales_meeting(attendees):
    """True when a sales mailbox was invited."""
    sales = set(sales_addresses())
    return any((a.get("email") or "").strip().lower() in sales for a in attendees or [])


def _notes_from(overview, summary):
    """Notes the AI extraction works from: the summary, plus Fireflies' own
    action items when it listed any."""
    actions = pick_action_items(summary)
    return overview + (f"\n\nAction items noted by Fireflies:\n{actions}" if actions else "")


def _refresh_incomplete(record, detail):
    """A meeting can sync before Fireflies has finished processing it, leaving
    the summary or transcript empty. Fill in whatever is now available, and
    update the conversation made from it if that was left blank too. Returns
    True when something changed."""
    summary = detail.get("summary") or {}
    overview = pick_overview(summary)
    transcript_text = build_transcript_text(detail.get("sentences"))
    changed = False
    highlights = pick_highlights(summary)
    actions = pick_action_items(summary)
    if highlights and not record.fireflies_highlights:
        record.fireflies_highlights = highlights
        changed = True
    if actions and not record.fireflies_action_items:
        record.fireflies_action_items = actions
        changed = True
    if overview and not record.fireflies_overview:
        record.fireflies_overview = overview
        changed = True
    if transcript_text and not record.transcript:
        record.transcript = transcript_text
        changed = True
    conv = Conversation.query.get(record.assigned_conversation_id) if record.assigned_conversation_id else None
    if conv:
        if overview and not (conv.raw_notes or "").strip():
            conv.raw_notes = _notes_from(overview, summary)
            changed = True
        if transcript_text and not conv.transcript:
            conv.transcript = transcript_text
            conv.transcript_status = "ready"
            changed = True
    return changed


def _sync_transcripts():
    meetings = fetch_recent_transcripts(limit=50)
    existing = {m.fireflies_id: m for m in FirefliesMeeting.query.all()}
    existing_ids = set(existing)
    refreshed = 0
    checks_left = 15  # cap on how many earlier meetings are re-checked per sync

    new_count = 0
    auto_matched_count = 0
    sales_count = 0

    for m in meetings:
        fid = m.get("id")
        if not fid:
            continue
        if fid in existing_ids:
            record = existing[fid]
            if checks_left > 0 and not (record.fireflies_overview and record.transcript and record.fireflies_highlights):
                checks_left -= 1
                if _refresh_incomplete(record, fetch_transcript_detail(fid)):
                    refreshed += 1
            continue

        detail = fetch_transcript_detail(fid)
        title = detail.get("title") or "Untitled Meeting"
        meeting_date = parse_fireflies_date(detail.get("date"))
        attendees = detail.get("meeting_attendees") or []
        participant_names = extract_participant_names(attendees)
        transcript_text = build_transcript_text(detail.get("sentences"))
        summary = detail.get("summary") or {}
        overview = pick_overview(summary)

        is_sales = _is_sales_meeting(attendees)
        # Sales meetings are prospects, not projects: never match them to a client.
        client = None if is_sales else guess_client_match(title, participant_names)

        record = FirefliesMeeting(
            fireflies_id=fid,
            title=title,
            meeting_date=meeting_date,
            duration_minutes=detail.get("duration"),
            participants=participant_names,
            transcript=transcript_text,
            fireflies_overview=overview,
            fireflies_highlights=pick_highlights(summary),
            fireflies_action_items=pick_action_items(summary),
            matched_client_id=client.id if client else None,
            synced_by_id=current_user.id if (client or is_sales) else None,
            status="sales_meeting" if is_sales else "uncategorized",
        )
        db.session.add(record)
        db.session.flush()
        new_count += 1

        if is_sales:
            sales_count += 1
            log_activity(
                current_user.id, None, "Meeting synced from Fireflies (sales)",
                "fireflies_meeting", record.id, details=title,
            )
        elif client:
            conversation = Conversation(
                client_id=client.id,
                interaction_type="Video Call",
                date=meeting_date,
                participants=participant_names,
                raw_notes=_notes_from(overview, summary),
                transcript=transcript_text,
                transcript_status="ready",
                source="fireflies",
                created_by_id=current_user.id,
            )
            db.session.add(conversation)
            db.session.flush()

            record.status = "assigned"
            record.assigned_client_id = client.id
            record.assigned_conversation_id = conversation.id
            auto_matched_count += 1

            log_activity(
                current_user.id, client.id, "Meeting synced from Fireflies (auto-matched)",
                "conversation", conversation.id, details=title,
            )
        else:
            log_activity(
                current_user.id, None, "Meeting synced from Fireflies (uncategorized)",
                "fireflies_meeting", record.id, details=title,
            )

    db.session.commit()
    return new_count, auto_matched_count, sales_count, refreshed

LAST_SYNC_KEY = "fireflies_last_sync"
LIMIT_UNTIL_KEY = "fireflies_limit_until"


def _set_setting(key, value):
    row = AppSetting.query.get(key)
    if row:
        row.value = value
    else:
        db.session.add(AppSetting(key=key, value=value))
    db.session.commit()


def _setting_time(key):
    row = AppSetting.query.get(key)
    if not row or not row.value:
        return None
    try:
        return datetime.fromisoformat(row.value)
    except ValueError:
        return None


def _is_limit_error(exc):
    text = str(exc).lower()
    return "too_many_requests" in text or "too many requests" in text or "429" in text or "rate limit" in text


def _next_midnight_utc():
    now = datetime.now(timezone.utc)
    return (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)


def time_ago(then):
    """'4 min ago' style text for a UTC timestamp (naive = UTC)."""
    if not then:
        return "Not synced yet"
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    mins = int((datetime.now(timezone.utc) - then).total_seconds() // 60)
    if mins < 1:
        return "Synced just now"
    if mins < 60:
        return f"Synced {mins} min ago"
    if mins < 60 * 24:
        return f"Synced {mins // 60} hr ago"
    return f"Synced {mins // (60 * 24)} d ago"


def starts_in(minutes):
    if minutes < 1:
        return "starting now"
    if minutes < 60:
        return f"in {minutes} min"
    if minutes < 60 * 24:
        return f"in {minutes // 60} hr"
    return f"in {minutes // (60 * 24)} d"


def _initials(name):
    parts = [p for p in (name or "").replace("@", " ").replace(".", " ").split() if p]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[1][0]).upper()


def _agenda(events, own_domain, sales):
    """Decorate calendar events for the Meetings page: where each call will be
    filed, who is outside the company, and how far away it is. Returns
    (next_up, days) where days groups the remaining events by date."""
    now = datetime.now(timezone.utc)
    for e in events:
        people = e.get("people") or []
        e["is_sales"] = any(p["email"] in sales for p in people)
        if e["is_sales"]:
            e["dest_kind"], e["dest_name"] = "sales", "Sales"
        else:
            client = guess_client_match(e["title"], [p["name"] or "" for p in people])
            if client:
                e["dest_kind"], e["dest_name"] = "client", client.name
            else:
                e["dest_kind"], e["dest_name"] = "review", "Needs review"
        e["avatars"] = [
            {"initials": _initials(p["name"]), "external": bool(own_domain) and not p["email"].endswith("@" + own_domain),
             "label": p["name"]}
            for p in people
        ][:4]
        e["more_people"] = max(0, len(people) - 4)
        start = e.get("start")
        e["minutes_away"] = None
        if start is not None and not e.get("is_all_day") and start.tzinfo is not None:
            e["minutes_away"] = int((start - now).total_seconds() // 60)

    next_up = next((e for e in events if e["minutes_away"] is not None and e["minutes_away"] >= -30), None)
    rest = [e for e in events if e is not next_up]
    days = []
    for e in rest:
        start = e.get("start")
        key = start.date() if start else None
        if not days or days[-1]["date"] != key:
            days.append({"date": key, "events": []})
        days[-1]["events"].append(e)
    return next_up, days


@fireflies_bp.route("")
@login_required
def inbox():
    pending = (
        FirefliesMeeting.query.filter_by(status="uncategorized")
        .order_by(FirefliesMeeting.meeting_date.desc())
        .all()
    )
    completed = (
        FirefliesMeeting.query.filter_by(status="assigned")
        .order_by(FirefliesMeeting.synced_at.desc())
        .limit(100)
        .all()
    )
    sales_meetings = (
        FirefliesMeeting.query.filter_by(status="sales_meeting")
        .order_by(FirefliesMeeting.synced_at.desc())
        .limit(100)
        .all()
    )
    clients = Client.query.order_by(Client.name).all()

    limit_until = _setting_time(LIMIT_UNTIL_KEY)
    if limit_until and limit_until.tzinfo is None:
        limit_until = limit_until.replace(tzinfo=timezone.utc)
    if limit_until and limit_until <= datetime.now(timezone.utc):
        limit_until = None

    calendar_connection = CalendarConnection.query.first()
    upcoming_events = []
    next_up, agenda_days = None, []
    calendar_error = None
    if calendar_connection:
        access_token = get_valid_access_token()
        if not access_token:
            calendar_error = "Your Google Calendar connection has expired. Please reconnect."
        else:
            try:
                raw_events = fetch_upcoming_events(access_token, max_results=10)
                upcoming_events = [parse_event(e) for e in raw_events]
                own_domain = (calendar_connection.email or "").rpartition("@")[2].lower()
                next_up, agenda_days = _agenda(upcoming_events, own_domain, set(sales_addresses()))
            except CalendarRequestError as exc:
                calendar_error = str(exc)

    return render_template(
        "fireflies_inbox.html",
        pending=pending, completed=completed, sales_meetings=sales_meetings, clients=clients,
        sales_addresses=sales_addresses(),
        parse_highlights=parse_highlights, parse_action_items=parse_action_items,
        calendar_connection=calendar_connection, upcoming_events=upcoming_events,
        next_up=next_up, agenda_days=agenda_days, starts_in=starts_in,
        calendar_error=calendar_error,
        last_synced=time_ago(_setting_time(LAST_SYNC_KEY)),
        limit_until=limit_until,
        fireflies_configured=bool(current_app.config.get("FIREFLIES_API_KEY")),
    )


@fireflies_bp.route("/sync", methods=["POST"])
@login_required
@limiter.limit("10 per minute")
def sync():
    try:
        new_count, auto_matched, sales_count, refreshed = _sync_transcripts()
    except FirefliesConfigError as exc:
        flash(str(exc), "error")
        return redirect(url_for("fireflies.inbox"))
    except FirefliesRequestError as exc:
        if _is_limit_error(exc):
            db.session.rollback()
            _set_setting(LIMIT_UNTIL_KEY, _next_midnight_utc().isoformat())
        else:
            flash(f"Fireflies sync failed: {exc}", "error")
        return redirect(url_for("fireflies.inbox"))

    _set_setting(LAST_SYNC_KEY, datetime.now(timezone.utc).isoformat())
    if new_count == 0:
        flash(
            f"No new meetings found. {refreshed} earlier meeting(s) had their summary or transcript filled in." if refreshed
            else "No new meetings found since the last sync.", "info",
        )
    else:
        review = new_count - auto_matched - sales_count
        flash(
            f"Synced {new_count} new meeting(s) - {auto_matched} auto-matched to a project, "
            f"{sales_count} filed under Sales, {review} need manual review below."
            + (f" {refreshed} earlier meeting(s) also had their summary filled in." if refreshed else ""),
            "success",
        )
    return redirect(url_for("fireflies.inbox"))


@fireflies_bp.route("/<int:meeting_id>/assign", methods=["POST"])
@login_required
def assign(meeting_id):
    meeting = FirefliesMeeting.query.get_or_404(meeting_id)
    raw_choice = request.form.get("client_id", "")

    # "Others / Sales" isn't a client - it's a category for meetings that
    # don't belong to any client engagement, so there's no Conversation to
    # create, just a status change. Kept as "sales_meeting" internally even
    # though the user-facing label is now "Others / Sales".
    if raw_choice == "sales_meeting":
        meeting.status = "sales_meeting"
        meeting.synced_by_id = current_user.id
        log_activity(
            current_user.id, None, "Fireflies meeting categorized as Others / Sales",
            "fireflies_meeting", meeting.id, details=meeting.title,
        )
        db.session.commit()
        flash(f'"{meeting.title}" moved to Others / Sales.', "success")
        return redirect(url_for("fireflies.inbox"))

    client_id = request.form.get("client_id", type=int)
    if not client_id:
        flash("Choose a project to move this meeting to.", "error")
        return redirect(url_for("fireflies.inbox"))
    client = Client.query.get_or_404(client_id)

    conversation = Conversation(
        client_id=client.id,
        interaction_type="Video Call",
        date=meeting.meeting_date,
        participants=meeting.participants,
        raw_notes=meeting.fireflies_overview or "",
        transcript=meeting.transcript,
        transcript_status="ready",
        source="fireflies",
        created_by_id=current_user.id,
    )
    db.session.add(conversation)
    db.session.flush()

    meeting.status = "assigned"
    meeting.assigned_client_id = client.id
    meeting.assigned_conversation_id = conversation.id
    meeting.synced_by_id = current_user.id

    log_activity(
        current_user.id, client.id, "Fireflies meeting moved to project",
        "conversation", conversation.id, details=meeting.title,
    )
    db.session.commit()

    flash(f'"{meeting.title}" moved to {client.name}.', "success")
    return redirect(url_for("conversations.detail", slug=client.slug, conversation_id=conversation.id))


@fireflies_bp.route("/<int:meeting_id>/revert", methods=["POST"])
@login_required
def revert(meeting_id):
    """Moves an "Others / Sales" meeting back to Pending, e.g. if it was
    filed there by mistake and actually belongs to a client. Only meaningful
    for that status - a meeting already tied to a client/Conversation via
    assign() isn't touched by this route."""
    meeting = FirefliesMeeting.query.get_or_404(meeting_id)
    if meeting.status != "sales_meeting":
        flash("Only an Others / Sales meeting can be moved back to Pending.", "error")
        return redirect(url_for("fireflies.inbox"))

    meeting.status = "uncategorized"
    meeting.synced_by_id = None
    log_activity(
        current_user.id, None, "Fireflies meeting moved back to Pending",
        "fireflies_meeting", meeting.id, details=meeting.title,
    )
    db.session.commit()
    flash(f'"{meeting.title}" moved back to Pending.', "success")
    return redirect(url_for("fireflies.inbox"))


@fireflies_bp.route("/<int:meeting_id>/ignore", methods=["POST"])
@login_required
def ignore(meeting_id):
    meeting = FirefliesMeeting.query.get_or_404(meeting_id)
    meeting.status = "ignored"
    log_activity(
        current_user.id, None, "Fireflies meeting dismissed",
        "fireflies_meeting", meeting.id, details=meeting.title,
    )
    db.session.commit()
    flash("Meeting dismissed.", "success")
    return redirect(url_for("fireflies.inbox"))


@fireflies_bp.route("/<int:meeting_id>/reviewed", methods=["POST"])
@login_required
def toggle_reviewed(meeting_id):
    """Mark a Sales meeting as reviewed, or back to New."""
    meeting = FirefliesMeeting.query.get_or_404(meeting_id)
    if meeting.status != "sales_meeting":
        flash("Only a Sales meeting can be marked as reviewed.", "error")
        return redirect(url_for("fireflies.inbox"))
    meeting.sales_reviewed_at = None if meeting.sales_reviewed_at else datetime.utcnow()
    log_activity(
        current_user.id, None,
        "Sales meeting marked as reviewed" if meeting.sales_reviewed_at else "Sales meeting marked as new",
        "fireflies_meeting", meeting.id, details=meeting.title,
    )
    db.session.commit()
    return redirect(url_for("fireflies.inbox") + "#sales")
