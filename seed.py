"""Seed the database with a login user and realistic demo data for
Sravana and Dron Imagination. Safe to re-run: skips anything that already exists.

Usage: python3 seed.py
"""
from datetime import datetime

from app import create_app
from extensions import db
from models import User, Client, Conversation, Decision, ActionItem, log_activity
from routes.clients import slugify

app = create_app()


def get_or_create_client(name, engagement_type):
    client = Client.query.filter_by(name=name).first()
    if client:
        return client
    client = Client(name=name, slug=slugify(name), engagement_type=engagement_type, is_demo=True)
    db.session.add(client)
    db.session.flush()
    return client


def add_conversation(client, user, interaction_type, date, participants, raw_notes, summary=None,
                      important_context=None, open_questions=None, decisions=None, action_items=None):
    conversation = Conversation(
        client_id=client.id,
        interaction_type=interaction_type,
        date=date,
        participants=participants,
        raw_notes=raw_notes,
        summary=summary,
        important_context=important_context,
        open_questions=open_questions or [],
        extraction_status="confirmed" if summary else "none",
        created_by_id=user.id,
        is_demo=True,
    )
    db.session.add(conversation)
    db.session.flush()

    source_label = f"{interaction_type} — {date.strftime('%d %b %Y')}"

    for d in decisions or []:
        db.session.add(Decision(
            client_id=client.id, conversation_id=conversation.id,
            decision=d["decision"], context=d.get("context", ""), owner=d.get("owner"),
            status=d.get("status", "Confirmed"), source_label=source_label, date=date,
        ))

    for a in action_items or []:
        db.session.add(ActionItem(
            client_id=client.id, conversation_id=conversation.id,
            task=a["task"], owner=a.get("owner", "Unassigned"), due_date=a.get("due_date"),
            priority=a.get("priority", "Medium"), status=a.get("status", "Not Started"),
            source_label=source_label, needs_confirmation=a.get("needs_confirmation", False),
        ))

    return conversation


def seed():
    with app.app_context():
        db.create_all()

        if not User.query.filter_by(email="anand@scaalex.com").first():
            user = User(name="Anand", email="anand@scaalex.com", role="advisor")
            user.set_password("Scaalex@2026")
            db.session.add(user)
            db.session.flush()
            print("Created login: anand@scaalex.com / Scaalex@2026")
        else:
            user = User.query.filter_by(email="anand@scaalex.com").first()

        # ---------------- Sravana ----------------
        if not Client.query.filter_by(name="Sravana").first():
            sravana = get_or_create_client("Sravana", "Digital Growth & Advisory Engagement")

            add_conversation(
                sravana, user, "Phone Call", datetime(2026, 9, 5, 11, 0),
                ["Anand", "Sravana CEO"],
                "Initial call to scope ongoing digital marketing support. CEO confirmed budget "
                "for Google Ads is currently fixed at the existing monthly level and asked for a "
                "performance review before committing to any increase.",
                summary="Scoped ongoing digital marketing support. Current Google Ads budget is fixed "
                        "at the existing monthly level pending a performance review.",
                important_context="Client is cautious about increasing ad spend without clear performance data.",
                decisions=[],
                action_items=[
                    {"task": "Prepare Google Ads performance review for client", "owner": "Scaalex/ShiftX",
                     "priority": "Medium", "status": "Completed"},
                ],
            )

            add_conversation(
                sravana, user, "Meeting", datetime(2026, 9, 12, 15, 30),
                ["Anand", "Sravana Management Team"],
                "Walked management through Google Ads performance over the last quarter. CTR and "
                "conversion rate both improved month-on-month. Management is inclined to continue "
                "the campaign but wants to see options for scaling spend ahead of the festive season.",
                summary="Reviewed Google Ads performance with management — CTR and conversions trending "
                        "up month-on-month. Management wants scaling options ahead of the festive season.",
                important_context="Festive season timing is a key driver for any budget increase decision.",
                open_questions=["What incremental budget would be needed to meaningfully scale before the festive season?"],
                decisions=[
                    {"decision": "Continue the current Google Ads campaign without interruption",
                     "context": "Performance metrics (CTR, conversions) are trending positively.",
                     "owner": "Sravana Management Team"},
                ],
                action_items=[
                    {"task": "Prepare scaling options and incremental budget scenarios for festive season",
                     "owner": "Scaalex/ShiftX", "priority": "High", "status": "Completed"},
                ],
            )

            add_conversation(
                sravana, user, "Meeting", datetime(2026, 9, 16, 16, 0),
                ["Anand", "Sravana Management Team"],
                "Discussed Google Ads performance, upcoming advertising budget and campaign "
                "requirements. Management confirmed intent to continue the campaign and will "
                "arrange additional advertising budget internally for the festive season push. "
                "Scaalex to prepare a fresh performance update ahead of the budget confirmation.",
                summary="Discussed Google Ads performance, upcoming advertising budget and campaign requirements.",
                important_context="Additional budget approval is expected internally within Sravana before end of month.",
                decisions=[
                    {"decision": "Continue campaign without interruption",
                     "context": "Campaign continuity was considered important while additional budget was being arranged.",
                     "owner": "Sravana Management Team"},
                    {"decision": "Additional advertising budget to be arranged",
                     "context": "Required to support festive season scaling discussed on 12 Sep.",
                     "owner": "Sravana Management Team", "status": "Needs Confirmation"},
                ],
                action_items=[
                    {"task": "Prepare campaign performance update", "owner": "Scaalex/ShiftX",
                     "due_date": datetime(2026, 9, 23).date(), "priority": "Medium"},
                    {"task": "Confirm additional advertising budget", "owner": "Client",
                     "due_date": datetime(2026, 9, 26).date(), "priority": "High"},
                ],
            )

            log_activity(user.id, sravana.id, "Demo data seeded", "client", sravana.id)

        # ---------------- Dron Imagination ----------------
        if not Client.query.filter_by(name="Dron Imagination").first():
            dron = get_or_create_client("Dron Imagination", "Capital Advisory Engagement")

            add_conversation(
                dron, user, "Video Call", datetime(2026, 8, 28, 10, 0),
                ["Anand", "Dron Imagination Founder", "Finance Head"],
                "Kickoff call to understand the current fundraising position. Founder outlined plans "
                "to raise a pre-Series A round and shared early traction numbers. Agreed Scaalex "
                "will prepare an initial positioning note before approaching investors.",
                summary="Kickoff call on the planned pre-Series A raise. Founder shared early traction; "
                        "Scaalex to prepare an investor positioning note first.",
                important_context="Founder is targeting a close within the next two quarters.",
                decisions=[
                    {"decision": "Proceed with pre-Series A fundraising process",
                     "context": "Founder confirmed intent and shared traction supporting a raise.",
                     "owner": "Dron Imagination Founder"},
                ],
                action_items=[
                    {"task": "Prepare investor positioning note", "owner": "Scaalex",
                     "due_date": datetime(2026, 9, 10).date(), "priority": "High", "status": "Completed"},
                ],
            )

            add_conversation(
                dron, user, "Meeting", datetime(2026, 9, 10, 14, 0),
                ["Anand", "Dron Imagination Founder"],
                "Reviewed the positioning note with the founder. Founder requested changes to the "
                "market sizing section and asked Scaalex to shortlist a first set of investors to "
                "approach. Timeline for outreach was not finalized.",
                summary="Reviewed investor positioning note; founder requested market sizing revisions "
                        "and asked for an initial investor shortlist.",
                open_questions=["Exact outreach start date — not yet finalized with the founder."],
                decisions=[],
                action_items=[
                    {"task": "Revise market sizing section of positioning note", "owner": "Scaalex",
                     "due_date": datetime(2026, 9, 18).date(), "priority": "Medium"},
                    {"task": "Shortlist initial investors for outreach", "owner": "Scaalex",
                     "due_date": datetime(2026, 9, 22).date(), "priority": "High"},
                ],
            )

            add_conversation(
                dron, user, "WhatsApp", datetime(2026, 9, 15, 9, 20),
                ["Anand", "Dron Imagination Founder"],
                "Founder confirmed market sizing revisions look good. Asked to hold on investor "
                "outreach until the board meeting on 25 September, where the round size will be finalized.",
                summary="Founder approved the revised positioning note but asked to pause investor "
                        "outreach until the round size is finalized at the 25 Sep board meeting.",
                important_context="Investor outreach is on hold pending the 25 Sep board meeting decision on round size.",
                decisions=[
                    {"decision": "Hold investor outreach until round size is finalized",
                     "context": "Founder wants board alignment on round size before approaching investors.",
                     "owner": "Dron Imagination Founder"},
                ],
                action_items=[
                    {"task": "Resume investor shortlist outreach after board meeting", "owner": "Scaalex",
                     "due_date": datetime(2026, 9, 26).date(), "priority": "Medium", "needs_confirmation": True},
                ],
            )

            log_activity(user.id, dron.id, "Demo data seeded", "client", dron.id)

        db.session.commit()
        print("Seed complete.")


if __name__ == "__main__":
    seed()
