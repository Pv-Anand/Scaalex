from datetime import datetime, time

from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user

from extensions import db, limiter
from models import Conversation, Decision, ActionItem, AIOverview, EmailDraft, log_activity
from routes.clients import get_client_or_404
from ai.anthropic_client import AIConfigError, AIRequestError
from ai.overview import generate_overview
from ai.email_draft import draft_email

ai_overview_bp = Blueprint("ai_overview", __name__, url_prefix="/clients/<slug>")


def _build_history_text(client, date_from=None, date_to=None):
    lines = []
    query = client.conversations
    if date_from:
        query = query.filter(Conversation.date >= datetime.combine(date_from, time.min))
    if date_to:
        query = query.filter(Conversation.date <= datetime.combine(date_to, time.max))
    conversations = query.order_by(Conversation.date.asc()).all()

    if date_from or date_to:
        lines.append(
            f"NOTE: This overview is scoped to conversations between "
            f"{date_from.strftime('%d %b %Y') if date_from else 'the beginning of the record'} and "
            f"{date_to.strftime('%d %b %Y') if date_to else 'today'}. "
            f"Base the executive summary, key decisions, and changes-since-last strictly on that window."
        )

    for c in conversations:
        lines.append(f"\n[{c.date.strftime('%d %b %Y')}] {c.interaction_type} — Participants: {c.participants_display or 'n/a'}")
        if c.summary:
            lines.append(f"Summary: {c.summary}")
        elif c.raw_notes:
            lines.append(f"Notes: {c.raw_notes}")
        if c.transcript and not c.summary:
            lines.append(f"Transcript excerpt: {c.transcript[:1500]}")
        if c.important_context:
            lines.append(f"Important context: {c.important_context}")
        if c.open_questions:
            lines.append(f"Open questions: {'; '.join(c.open_questions)}")

        decisions = c.decisions.all()
        if decisions:
            lines.append("Decisions from this conversation: " + "; ".join(
                f"{d.decision} (owner: {d.owner or 'n/a'}, status: {d.status})" for d in decisions
            ))
        items = c.action_items.all()
        if items:
            lines.append("Action items from this conversation: " + "; ".join(
                f"{a.task} (owner: {a.owner}, due: {a.due_date or 'n/a'}, status: {a.status})" for a in items
            ))

    all_actions = client.action_items.order_by(ActionItem.due_date.is_(None), ActionItem.due_date.asc()).all()
    if all_actions:
        lines.append(
            "\n--- Current status of all action items (live snapshot, not limited to the "
            "period above) ---"
        )
        for a in all_actions:
            lines.append(f"- {a.task} | owner: {a.owner} | due: {a.due_date or 'n/a'} | priority: {a.priority} | status: {a.status}")

    return "\n".join(lines) if lines else "No conversations have been documented for this client yet."


def _previous_overview_text(overview: AIOverview):
    if not overview:
        return None
    parts = [f"Generated: {overview.generated_at.strftime('%d %b %Y')}", f"Executive summary: {overview.executive_summary}"]
    if overview.key_decisions:
        parts.append("Key decisions: " + "; ".join(d.get("decision", "") for d in overview.key_decisions))
    if overview.key_actions:
        parts.append("Key actions: " + "; ".join(a.get("action", "") for a in overview.key_actions))
    if overview.leadership_notes:
        parts.append(f"Leadership notes: {overview.leadership_notes}")
    return "\n".join(parts)


@ai_overview_bp.route("/ai-summary")
@login_required
def ai_summary(slug):
    client = get_client_or_404(slug)
    latest = client.overviews.order_by(AIOverview.generated_at.desc()).first()
    history = client.overviews.order_by(AIOverview.generated_at.desc()).offset(1).limit(10).all()
    return render_template(
        "ai_summary.html", client=client, active_tab="ai_summary",
        latest=latest, history=history,
        has_data=client.conversation_count > 0,
    )


@ai_overview_bp.route("/ai-summary/generate", methods=["POST"])
@login_required
@limiter.limit("10 per minute")
def generate(slug):
    client = get_client_or_404(slug)

    if client.conversation_count == 0:
        flash("Add at least one conversation before generating a leadership overview.", "error")
        return redirect(url_for("ai_overview.ai_summary", slug=slug))

    def _parse_date(field):
        raw = request.form.get(field, "").strip()
        if not raw:
            return None
        try:
            return datetime.strptime(raw, "%Y-%m-%d").date()
        except ValueError:
            flash("That date range wasn't valid - generated for the full history instead.", "error")
            return None

    date_from = _parse_date("date_from")
    date_to = _parse_date("date_to")

    if date_from and date_to and date_from > date_to:
        date_from, date_to = date_to, date_from

    if date_from or date_to:
        in_range = client.conversations
        if date_from:
            in_range = in_range.filter(Conversation.date >= datetime.combine(date_from, time.min))
        if date_to:
            in_range = in_range.filter(Conversation.date <= datetime.combine(date_to, time.max))
        if in_range.count() == 0:
            flash("No conversations fall within that date range.", "error")
            return redirect(url_for("ai_overview.ai_summary", slug=slug))

    previous = client.overviews.order_by(AIOverview.generated_at.desc()).first()
    history_text = _build_history_text(client, date_from, date_to)
    previous_text = _previous_overview_text(previous)

    try:
        result = generate_overview(client.name, history_text, previous_text)
    except AIConfigError as exc:
        flash(str(exc), "error")
        return redirect(url_for("ai_overview.ai_summary", slug=slug))
    except AIRequestError as exc:
        flash(f"Overview generation failed: {exc}", "error")
        return redirect(url_for("ai_overview.ai_summary", slug=slug))

    overview = AIOverview(
        client_id=client.id,
        period_start=date_from,
        period_end=date_to,
        executive_summary=result.get("executive_summary", ""),
        key_actions=result.get("key_actions", []),
        key_decisions=result.get("key_decisions", []),
        risks=result.get("risks", []),
        leadership_notes=result.get("leadership_notes", ""),
        suggested_next_steps=result.get("suggested_next_steps", []),
        changes_since_last=result.get("changes_since_last", ""),
        generated_by_id=current_user.id,
    )
    db.session.add(overview)
    db.session.flush()
    log_activity(current_user.id, client.id, "Leadership overview generated", "ai_overview", overview.id)
    db.session.commit()

    flash("Leadership overview generated.", "success")
    return redirect(url_for("ai_overview.view_overview", slug=slug, overview_id=overview.id))


@ai_overview_bp.route("/ai-summary/<int:overview_id>")
@login_required
def view_overview(slug, overview_id):
    client = get_client_or_404(slug)
    overview = AIOverview.query.filter_by(id=overview_id, client_id=client.id).first_or_404()
    latest_draft = overview.email_drafts.order_by(EmailDraft.created_at.desc()).first()
    return render_template(
        "ai_overview_detail.html", client=client, active_tab="ai_summary",
        overview=overview, latest_draft=latest_draft,
    )


@ai_overview_bp.route("/ai-summary/<int:overview_id>/email", methods=["POST"])
@login_required
@limiter.limit("20 per minute")
def generate_email(slug, overview_id):
    client = get_client_or_404(slug)
    overview = AIOverview.query.filter_by(id=overview_id, client_id=client.id).first_or_404()

    context_parts = [f"Executive summary: {overview.executive_summary}"]
    if overview.key_decisions:
        context_parts.append("Decisions: " + "; ".join(
            f"{d.get('decision')} ({d.get('context', '')})" for d in overview.key_decisions
        ))
    if overview.key_actions:
        context_parts.append("Action items: " + "; ".join(
            f"{a.get('action')} - owner: {a.get('owner')}, due: {a.get('due_date') or 'tbc'}" for a in overview.key_actions
        ))
    if overview.suggested_next_steps:
        context_parts.append("Suggested next steps: " + "; ".join(overview.suggested_next_steps))
    context_text = "\n".join(context_parts)

    advisor_name = current_user.name if current_user.is_authenticated else None

    try:
        result = draft_email(client.name, context_text, advisor_name)
    except AIConfigError as exc:
        return jsonify({"error": str(exc), "config_error": True}), 200
    except AIRequestError as exc:
        return jsonify({"error": str(exc)}), 502

    draft = EmailDraft(
        client_id=client.id,
        overview_id=overview.id,
        subject=result.get("subject", ""),
        body=result.get("body", ""),
    )
    db.session.add(draft)
    log_activity(current_user.id, client.id, "Client email draft generated", "email_draft")
    db.session.commit()

    return jsonify({"id": draft.id, "subject": draft.subject, "body": draft.body})
