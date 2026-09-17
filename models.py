import json
from datetime import datetime

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from extensions import db


def _now():
    return datetime.utcnow()


class JSONText(db.TypeDecorator):
    """Store Python lists/dicts as JSON text in SQLite."""

    impl = db.Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return json.dumps([])
        return json.dumps(value)

    def process_result_value(self, value, dialect):
        if not value:
            return []
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return []


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(200), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(40), default="advisor")
    created_at = db.Column(db.DateTime, default=_now)

    def set_password(self, password):
        # pbkdf2:sha256 explicitly, since this environment's Python is built against
        # LibreSSL, which lacks the scrypt support werkzeug defaults to.
        self.password_hash = generate_password_hash(password, method="pbkdf2:sha256")

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class Client(db.Model):
    __tablename__ = "clients"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    slug = db.Column(db.String(200), unique=True, nullable=False)
    status = db.Column(db.String(40), default="Active")  # Active, Paused, Closed
    engagement_type = db.Column(db.String(120), default="Advisory Engagement")
    is_demo = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=_now)
    updated_at = db.Column(db.DateTime, default=_now, onupdate=_now)

    conversations = db.relationship(
        "Conversation", backref="client", lazy="dynamic",
        cascade="all, delete-orphan", order_by="desc(Conversation.date)",
    )
    decisions = db.relationship(
        "Decision", backref="client", lazy="dynamic", cascade="all, delete-orphan",
        order_by="desc(Decision.date)",
    )
    action_items = db.relationship(
        "ActionItem", backref="client", lazy="dynamic", cascade="all, delete-orphan",
    )
    documents = db.relationship(
        "Document", backref="client", lazy="dynamic", cascade="all, delete-orphan",
    )
    overviews = db.relationship(
        "AIOverview", backref="client", lazy="dynamic", cascade="all, delete-orphan",
        order_by="desc(AIOverview.generated_at)",
    )

    @property
    def last_activity(self):
        latest = self.conversations.order_by(Conversation.date.desc()).first()
        return latest.date if latest else None

    @property
    def open_action_items_count(self):
        return self.action_items.filter(ActionItem.status != "Completed").count()

    @property
    def overdue_action_items_count(self):
        today = datetime.utcnow().date()
        return self.action_items.filter(
            ActionItem.status != "Completed",
            ActionItem.due_date.isnot(None),
            ActionItem.due_date < today,
        ).count()

    @property
    def conversation_count(self):
        return self.conversations.count()

    @property
    def decision_count(self):
        return self.decisions.count()


class Conversation(db.Model):
    __tablename__ = "conversations"

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False)
    interaction_type = db.Column(db.String(60), nullable=False)  # Meeting, Phone Call, ...
    date = db.Column(db.DateTime, nullable=False, default=_now)
    participants = db.Column(JSONText, default=list)  # list[str]
    raw_notes = db.Column(db.Text, default="")

    audio_filename = db.Column(db.String(300))
    audio_original_name = db.Column(db.String(300))
    transcript = db.Column(db.Text)
    transcript_status = db.Column(db.String(40), default="none")  # none, pending, ready, failed

    # AI extraction (unconfirmed) stored as JSON until advisor reviews it
    ai_extraction = db.Column(JSONText, default=dict)
    extraction_status = db.Column(db.String(40), default="none")  # none, pending_review, confirmed, discarded

    summary = db.Column(db.Text)  # confirmed discussion summary shown on timeline
    important_context = db.Column(db.Text)
    open_questions = db.Column(JSONText, default=list)

    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    is_demo = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=_now)
    updated_at = db.Column(db.DateTime, default=_now, onupdate=_now)

    created_by = db.relationship("User")
    decisions = db.relationship("Decision", backref="conversation", lazy="dynamic")
    action_items = db.relationship("ActionItem", backref="conversation", lazy="dynamic")
    documents = db.relationship("Document", backref="conversation", lazy="dynamic")

    @property
    def participants_display(self):
        return ", ".join(self.participants or [])


class Decision(db.Model):
    __tablename__ = "decisions"

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False)
    conversation_id = db.Column(db.Integer, db.ForeignKey("conversations.id"))

    decision = db.Column(db.String(500), nullable=False)
    context = db.Column(db.Text)
    date = db.Column(db.DateTime, default=_now)
    owner = db.Column(db.String(200))
    status = db.Column(db.String(40), default="Confirmed")  # Confirmed, Needs Confirmation
    source_label = db.Column(db.String(300))  # e.g. "Client Meeting - 16 Sep 2026"

    created_at = db.Column(db.DateTime, default=_now)


class ActionItem(db.Model):
    __tablename__ = "action_items"

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False)
    conversation_id = db.Column(db.Integer, db.ForeignKey("conversations.id"))

    task = db.Column(db.String(500), nullable=False)
    owner = db.Column(db.String(200))
    due_date = db.Column(db.Date)
    priority = db.Column(db.String(20), default="Medium")  # High, Medium, Low
    status = db.Column(db.String(40), default="Not Started")
    source_label = db.Column(db.String(300))
    needs_confirmation = db.Column(db.Boolean, default=False)

    created_at = db.Column(db.DateTime, default=_now)
    updated_at = db.Column(db.DateTime, default=_now, onupdate=_now)

    @property
    def is_overdue(self):
        return (
            self.status != "Completed"
            and self.due_date is not None
            and self.due_date < datetime.utcnow().date()
        )


class Document(db.Model):
    __tablename__ = "documents"

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False)
    conversation_id = db.Column(db.Integer, db.ForeignKey("conversations.id"))

    file_name = db.Column(db.String(300), nullable=False)
    stored_name = db.Column(db.String(300), nullable=False)
    file_type = db.Column(db.String(40))
    file_size = db.Column(db.Integer)
    uploaded_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    uploaded_at = db.Column(db.DateTime, default=_now)

    uploaded_by = db.relationship("User")


class AIOverview(db.Model):
    __tablename__ = "ai_overviews"

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False)
    generated_at = db.Column(db.DateTime, default=_now)

    executive_summary = db.Column(db.Text)
    key_actions = db.Column(JSONText, default=list)
    key_decisions = db.Column(JSONText, default=list)
    risks = db.Column(JSONText, default=list)
    leadership_notes = db.Column(db.Text)
    suggested_next_steps = db.Column(JSONText, default=list)
    changes_since_last = db.Column(db.Text)

    generated_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    generated_by = db.relationship("User")

    email_drafts = db.relationship("EmailDraft", backref="overview", lazy="dynamic")


class EmailDraft(db.Model):
    __tablename__ = "email_drafts"

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False)
    overview_id = db.Column(db.Integer, db.ForeignKey("ai_overviews.id"))
    conversation_id = db.Column(db.Integer, db.ForeignKey("conversations.id"))

    subject = db.Column(db.String(300))
    body = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=_now)


class AuditLog(db.Model):
    __tablename__ = "audit_log"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"))
    action = db.Column(db.String(120), nullable=False)
    entity_type = db.Column(db.String(60))
    entity_id = db.Column(db.Integer)
    details = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=_now)

    user = db.relationship("User")


def log_activity(user_id, client_id, action, entity_type=None, entity_id=None, details=None):
    entry = AuditLog(
        user_id=user_id, client_id=client_id, action=action,
        entity_type=entity_type, entity_id=entity_id, details=details,
    )
    db.session.add(entry)
