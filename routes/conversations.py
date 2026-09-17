from datetime import datetime

from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user

from extensions import db
from models import Conversation, Decision, ActionItem, log_activity
from routes.clients import get_client_or_404

conversations_bp = Blueprint("conversations", __name__, url_prefix="/clients/<slug>/conversations")

INTERACTION_TYPES = [
    "Meeting", "Phone Call", "WhatsApp", "Email", "Video Call", "Internal Discussion", "Other",
]


@conversations_bp.route("")
@login_required
def list_conversations(slug):
    client = get_client_or_404(slug)
    q = Conversation.query.filter_by(client_id=client.id)

    itype = request.args.get("type")
    if itype:
        q = q.filter(Conversation.interaction_type == itype)

    person = request.args.get("person", "").strip()
    conversations = q.order_by(Conversation.date.desc()).all()
    if person:
        conversations = [c for c in conversations if any(person.lower() in p.lower() for p in (c.participants or []))]

    return render_template(
        "conversations_list.html", client=client, active_tab="conversations",
        conversations=conversations, interaction_types=INTERACTION_TYPES,
        current_type=itype, current_person=person,
    )


@conversations_bp.route("/timeline")
@login_required
def timeline(slug):
    client = get_client_or_404(slug)
    q = Conversation.query.filter_by(client_id=client.id)

    itype = request.args.get("type")
    if itype:
        q = q.filter(Conversation.interaction_type == itype)

    person = request.args.get("person", "").strip()
    date_from = request.args.get("from")
    date_to = request.args.get("to")
    if date_from:
        try:
            q = q.filter(Conversation.date >= datetime.strptime(date_from, "%Y-%m-%d"))
        except ValueError:
            pass
    if date_to:
        try:
            q = q.filter(Conversation.date <= datetime.strptime(date_to, "%Y-%m-%d"))
        except ValueError:
            pass

    conversations = q.order_by(Conversation.date.desc()).all()
    if person:
        conversations = [c for c in conversations if any(person.lower() in p.lower() for p in (c.participants or []))]

    keyword = request.args.get("q", "").strip().lower()
    if keyword:
        def matches(c):
            haystacks = [c.raw_notes or "", c.summary or "", c.transcript or ""]
            haystacks += [d.decision for d in c.decisions]
            haystacks += [a.task for a in c.action_items]
            return any(keyword in h.lower() for h in haystacks)
        conversations = [c for c in conversations if matches(c)]

    return render_template(
        "timeline.html", client=client, active_tab="timeline",
        conversations=conversations, interaction_types=INTERACTION_TYPES,
        filters=request.args,
    )


@conversations_bp.route("/new", methods=["GET", "POST"])
@login_required
def new(slug):
    client = get_client_or_404(slug)

    if request.method == "POST":
        interaction_type = request.form.get("interaction_type", "Meeting")
        date_str = request.form.get("date")
        try:
            date = datetime.strptime(date_str, "%Y-%m-%dT%H:%M") if date_str else datetime.utcnow()
        except ValueError:
            date = datetime.utcnow()

        participants = [p.strip() for p in request.form.get("participants", "").split(",") if p.strip()]
        raw_notes = request.form.get("raw_notes", "").strip()

        conversation = Conversation(
            client_id=client.id,
            interaction_type=interaction_type,
            date=date,
            participants=participants,
            raw_notes=raw_notes,
            created_by_id=current_user.id,
        )
        db.session.add(conversation)
        db.session.flush()
        log_activity(current_user.id, client.id, "Update created", "conversation", conversation.id,
                     details=f"{interaction_type} on {date.strftime('%d %b %Y')}")
        db.session.commit()
        flash("Update recorded.", "success")
        return redirect(url_for("conversations.detail", slug=slug, conversation_id=conversation.id))

    return render_template(
        "conversation_new.html", client=client, active_tab="conversations",
        interaction_types=INTERACTION_TYPES, now=datetime.utcnow(),
    )


@conversations_bp.route("/<int:conversation_id>")
@login_required
def detail(slug, conversation_id):
    client = get_client_or_404(slug)
    conversation = Conversation.query.filter_by(id=conversation_id, client_id=client.id).first_or_404()
    return render_template(
        "conversation_detail.html", client=client, active_tab="conversations",
        conversation=conversation,
    )


@conversations_bp.route("/<int:conversation_id>/edit", methods=["GET", "POST"])
@login_required
def edit(slug, conversation_id):
    client = get_client_or_404(slug)
    conversation = Conversation.query.filter_by(id=conversation_id, client_id=client.id).first_or_404()

    if request.method == "POST":
        conversation.interaction_type = request.form.get("interaction_type", conversation.interaction_type)
        date_str = request.form.get("date")
        if date_str:
            try:
                conversation.date = datetime.strptime(date_str, "%Y-%m-%dT%H:%M")
            except ValueError:
                pass
        conversation.participants = [p.strip() for p in request.form.get("participants", "").split(",") if p.strip()]
        conversation.raw_notes = request.form.get("raw_notes", "").strip()

        if conversation.extraction_status == "confirmed":
            conversation.summary = request.form.get("summary", "").strip()
            conversation.important_context = request.form.get("important_context", "").strip()
            open_questions_raw = request.form.get("open_questions", "")
            conversation.open_questions = [q.strip() for q in open_questions_raw.splitlines() if q.strip()]

        log_activity(current_user.id, client.id, "Update edited", "conversation", conversation.id)
        db.session.commit()
        flash("Update saved.", "success")
        return redirect(url_for("conversations.detail", slug=slug, conversation_id=conversation.id))

    return render_template(
        "conversation_edit.html", client=client, active_tab="conversations",
        conversation=conversation, interaction_types=INTERACTION_TYPES,
    )


@conversations_bp.route("/<int:conversation_id>/transcript", methods=["POST"])
@login_required
def save_transcript(slug, conversation_id):
    client = get_client_or_404(slug)
    conversation = Conversation.query.filter_by(id=conversation_id, client_id=client.id).first_or_404()
    conversation.transcript = request.form.get("transcript", "")
    conversation.transcript_status = "ready"
    log_activity(current_user.id, client.id, "Transcript edited", "conversation", conversation.id)
    db.session.commit()
    if request.headers.get("X-Requested-With") == "fetch":
        return jsonify({"ok": True})
    flash("Transcript saved.", "success")
    return redirect(url_for("conversations.detail", slug=slug, conversation_id=conversation_id))


@conversations_bp.route("/<int:conversation_id>/confirm-extraction", methods=["POST"])
@login_required
def confirm_extraction(slug, conversation_id):
    client = get_client_or_404(slug)
    conversation = Conversation.query.filter_by(id=conversation_id, client_id=client.id).first_or_404()
    payload = request.get_json(force=True)

    conversation.summary = payload.get("summary", conversation.summary)
    conversation.important_context = payload.get("important_context", conversation.important_context)
    conversation.open_questions = payload.get("open_questions", [])

    source_label = f"{conversation.interaction_type} — {conversation.date.strftime('%d %b %Y')}"

    for d in payload.get("decisions", []):
        if not d.get("include"):
            continue
        decision = Decision(
            client_id=client.id,
            conversation_id=conversation.id,
            decision=d.get("decision", "").strip(),
            context=d.get("context", "").strip(),
            owner=d.get("owner", "").strip() or None,
            status="Needs Confirmation" if d.get("needs_confirmation") else "Confirmed",
            source_label=source_label,
            date=conversation.date,
        )
        if decision.decision:
            db.session.add(decision)

    for a in payload.get("action_items", []):
        if not a.get("include"):
            continue
        due_date = None
        if a.get("due_date"):
            try:
                due_date = datetime.strptime(a["due_date"], "%Y-%m-%d").date()
            except ValueError:
                due_date = None
        item = ActionItem(
            client_id=client.id,
            conversation_id=conversation.id,
            task=a.get("task", "").strip(),
            owner=a.get("owner", "").strip() or "Unassigned",
            due_date=due_date,
            priority=a.get("priority", "Medium"),
            status="Not Started",
            source_label=source_label,
            needs_confirmation=bool(a.get("needs_confirmation")),
        )
        if item.task:
            db.session.add(item)

    conversation.extraction_status = "confirmed"
    log_activity(current_user.id, client.id, "AI extraction confirmed", "conversation", conversation.id)
    db.session.commit()
    return jsonify({"ok": True, "redirect": url_for("conversations.detail", slug=slug, conversation_id=conversation.id)})


@conversations_bp.route("/<int:conversation_id>/discard-extraction", methods=["POST"])
@login_required
def discard_extraction(slug, conversation_id):
    client = get_client_or_404(slug)
    conversation = Conversation.query.filter_by(id=conversation_id, client_id=client.id).first_or_404()
    conversation.ai_extraction = {}
    conversation.extraction_status = "discarded"
    log_activity(current_user.id, client.id, "AI extraction discarded", "conversation", conversation.id)
    db.session.commit()
    return jsonify({"ok": True})
