from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app
from flask_login import login_required, current_user

from extensions import db, limiter
from models import Client, Conversation, FirefliesMeeting, CalendarConnection, log_activity
from ai.fireflies_client import (
    FirefliesConfigError, FirefliesRequestError,
    fetch_recent_transcripts, fetch_transcript_detail,
    parse_fireflies_date, extract_participant_names, build_transcript_text,
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


def _is_sales_meeting(attendees):
    """True when a sales mailbox (sales@scaalex.com by default) was invited."""
    sales = set(current_app.config.get("SALES_MEETING_EMAILS") or ())
    return any((a.get("email") or "").strip().lower() in sales for a in attendees or [])


def _sync_transcripts():
    meetings = fetch_recent_transcripts(limit=25)
    existing_ids = {m.fireflies_id for m in FirefliesMeeting.query.all()}

    new_count = 0
    auto_matched_count = 0
    sales_count = 0

    for m in meetings:
        fid = m.get("id")
        if not fid or fid in existing_ids:
            continue

        detail = fetch_transcript_detail(fid)
        title = detail.get("title") or "Untitled Meeting"
        meeting_date = parse_fireflies_date(detail.get("date"))
        attendees = detail.get("meeting_attendees") or []
        participant_names = extract_participant_names(attendees)
        transcript_text = build_transcript_text(detail.get("sentences"))
        summary = detail.get("summary") or {}
        overview = summary.get("short_overview") or ""

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
                raw_notes=overview,
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
    return new_count, auto_matched_count, sales_count


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

    calendar_connection = CalendarConnection.query.first()
    upcoming_events = []
    calendar_error = None
    if calendar_connection:
        access_token = get_valid_access_token()
        if not access_token:
            calendar_error = "Your Google Calendar connection has expired. Please reconnect."
        else:
            try:
                raw_events = fetch_upcoming_events(access_token, max_results=10)
                upcoming_events = [parse_event(e) for e in raw_events]
                sales = set(current_app.config.get("SALES_MEETING_EMAILS") or ())
                for e in upcoming_events:
                    e["is_sales"] = any(x in sales for x in e.get("attendee_emails", []))
            except CalendarRequestError as exc:
                calendar_error = str(exc)

    return render_template(
        "fireflies_inbox.html",
        pending=pending, completed=completed, sales_meetings=sales_meetings, clients=clients,
        calendar_connection=calendar_connection, upcoming_events=upcoming_events,
        calendar_error=calendar_error,
    )


@fireflies_bp.route("/sync", methods=["POST"])
@login_required
@limiter.limit("10 per minute")
def sync():
    try:
        new_count, auto_matched, sales_count = _sync_transcripts()
    except FirefliesConfigError as exc:
        flash(str(exc), "error")
        return redirect(url_for("fireflies.inbox"))
    except FirefliesRequestError as exc:
        flash(f"Fireflies sync failed: {exc}", "error")
        return redirect(url_for("fireflies.inbox"))

    if new_count == 0:
        flash("No new meetings found since the last sync.", "info")
    else:
        review = new_count - auto_matched - sales_count
        flash(
            f"Synced {new_count} new meeting(s) - {auto_matched} auto-matched to a project, "
            f"{sales_count} filed under Sales, {review} need manual review below.",
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
