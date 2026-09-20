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


ROLES = ["owner", "admin", "executive"]
ROLE_LABELS = {"owner": "Owner", "admin": "Administrator", "executive": "Executive"}


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(200), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(40), default="executive")
    created_at = db.Column(db.DateTime, default=_now)

    def set_password(self, password):
        # pbkdf2:sha256 explicitly, since this environment's Python is built against
        # LibreSSL, which lacks the scrypt support werkzeug defaults to.
        self.password_hash = generate_password_hash(password, method="pbkdf2:sha256")

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def role_label(self):
        return ROLE_LABELS.get(self.role, self.role)

    @property
    def is_owner(self):
        return self.role == "owner"

    @property
    def is_admin(self):
        # Owner is a superset of Administrator - both manage the team and
        # bypass per-client access grants. Only Executives are scoped by
        # ClientAccess rows.
        return self.role in ("owner", "admin")

    def can_view_client(self, client_id):
        if self.is_admin:
            return True
        return ClientAccess.query.filter_by(user_id=self.id, client_id=client_id).first() is not None

    def can_edit_client(self, client_id):
        if self.is_admin:
            return True
        grant = ClientAccess.query.filter_by(user_id=self.id, client_id=client_id).first()
        return grant is not None and grant.access_level == "edit"


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

    # Client Profile fields
    registered_name = db.Column(db.String(300))
    address = db.Column(db.Text)
    website = db.Column(db.String(300))
    gst_number = db.Column(db.String(40))

    # Client portal - a separate slug from `slug` above (which is the staff
    # URL) so the client-facing link can be edited/rotated independently.
    portal_slug = db.Column(db.String(200), unique=True)

    contacts = db.relationship(
        "ClientContact", backref="client", lazy="dynamic",
        cascade="all, delete-orphan",
    )
    milestones = db.relationship(
        "Milestone", backref="client", lazy="dynamic", cascade="all, delete-orphan",
    )

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


class ClientAccess(db.Model):
    """Grants an Executive view or edit access to one client. Owners and
    Administrators never need a row here - User.is_admin bypasses this
    entirely, so this table only ever matters for role == "executive"."""

    __tablename__ = "client_access"
    __table_args__ = (db.UniqueConstraint("user_id", "client_id", name="uq_client_access_user_client"),)

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False)
    access_level = db.Column(db.String(10), nullable=False)  # view, edit
    created_at = db.Column(db.DateTime, default=_now)

    user = db.relationship("User", backref="client_access_grants")
    client = db.relationship("Client")


class ClientContact(db.Model):
    """A point of contact on the client side. Any number can exist per
    client; at most one is marked primary at a time (enforced in
    routes/clients.py, not at the DB level, to keep this a plain flag).

    Doubles as the client portal's login identity when portal_access is on -
    deliberately not a second User-like table, since the portal only ever
    needs to identify "this contact, for this one client", never roles or
    cross-client access the way staff accounts do."""

    __tablename__ = "client_contacts"

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False)

    name = db.Column(db.String(200), nullable=False)
    email = db.Column(db.String(200))
    phone = db.Column(db.String(40))
    designation = db.Column(db.String(120))
    is_primary = db.Column(db.Boolean, default=False)

    # Portal access
    portal_access = db.Column(db.Boolean, default=False)
    password_hash = db.Column(db.String(255))
    must_change_password = db.Column(db.Boolean, default=True)
    last_login_at = db.Column(db.DateTime)
    terms_accepted_at = db.Column(db.DateTime)

    created_at = db.Column(db.DateTime, default=_now)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password, method="pbkdf2:sha256")

    def check_password(self, password):
        if not self.password_hash:
            return False
        return check_password_hash(self.password_hash, password)


class Conversation(db.Model):
    __tablename__ = "conversations"

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False)
    interaction_type = db.Column(db.String(60), nullable=False)  # Meeting, Phone Call, ...
    date = db.Column(db.DateTime, nullable=False, default=_now)
    participants = db.Column(JSONText, default=list)  # list[str]
    raw_notes = db.Column(db.Text, default="")

    transcript = db.Column(db.Text)
    transcript_status = db.Column(db.String(40), default="none")  # none, ready

    # AI extraction (unconfirmed) stored as JSON until advisor reviews it
    ai_extraction = db.Column(JSONText, default=dict)
    extraction_status = db.Column(db.String(40), default="none")  # none, pending_review, confirmed, discarded

    summary = db.Column(db.Text)  # confirmed discussion summary shown on timeline
    important_context = db.Column(db.Text)
    open_questions = db.Column(JSONText, default=list)

    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    is_demo = db.Column(db.Boolean, default=False)
    source = db.Column(db.String(20), default="manual")  # manual, fireflies
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
    assignee = db.Column(db.String(200))
    due_date = db.Column(db.Date)
    priority = db.Column(db.String(20), default="Medium")  # High, Medium, Low
    status = db.Column(db.String(40), default="Not Started")
    notes = db.Column(db.Text)
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


class Milestone(db.Model):
    """One step of the engagement timeline shown to the client on their
    portal. Ordering is derived, not stored: completed ones sort by `date`
    (most recent first), in-progress/upcoming ones sort by `due_date` -
    matches how the timeline reads naturally without a manual reorder UI."""

    __tablename__ = "milestones"

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False)

    title = db.Column(db.String(300), nullable=False)
    status = db.Column(db.String(20), default="upcoming")  # upcoming, in_progress, completed
    date = db.Column(db.Date)  # completion date, set when status becomes "completed"
    due_date = db.Column(db.Date)  # target date while upcoming/in_progress

    reporting_manager_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    visible_to_client = db.Column(db.Boolean, default=True)

    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=_now)
    updated_at = db.Column(db.DateTime, default=_now, onupdate=_now)

    reporting_manager = db.relationship("User", foreign_keys=[reporting_manager_id])
    created_by = db.relationship("User", foreign_keys=[created_by_id])
    requests = db.relationship(
        "MilestoneRequest", backref="milestone", lazy="dynamic",
        cascade="all, delete-orphan", order_by="desc(MilestoneRequest.requested_at)",
    )
    deliverables = db.relationship(
        "Document", backref="milestone", lazy="dynamic",
        order_by="desc(Document.uploaded_at)",
    )

    @property
    def active_request(self):
        return self.requests.filter_by(status="awaiting").first()

    @property
    def display_date(self):
        return self.date if self.status == "completed" else self.due_date

    @property
    def staff_deliverables(self):
        """Files Scaalex has attached for the client to download - excludes
        the client's own request responses, which live under `requests`
        instead even though they share the same `deliverables` relationship
        (both are just Documents with this milestone_id)."""
        return self.deliverables.filter_by(uploaded_by_contact_id=None).all()


class MilestoneRequest(db.Model):
    """Something staff has asked the client for, tied to one milestone -
    shows as an action item on the client's portal until fulfilled, then as
    "Under Review" on both sides until a staff member explicitly accepts or
    rejects it. Accept -> "Received" is a terminal acknowledgment. Reject
    -> "Rejected" is also terminal for THIS row (keeps the submission and
    the reason on record), but automatically reopens the same ask as a
    fresh awaiting request so the client sees why and can resubmit."""

    __tablename__ = "milestone_requests"

    id = db.Column(db.Integer, primary_key=True)
    milestone_id = db.Column(db.Integer, db.ForeignKey("milestones.id"), nullable=False)

    request_type = db.Column(db.String(20), nullable=False)  # data, url, document
    message = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(20), default="awaiting")  # awaiting, fulfilled, received, rejected

    response_text = db.Column(db.Text)
    response_url = db.Column(db.String(500))
    response_document_id = db.Column(db.Integer, db.ForeignKey("documents.id"))

    requested_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    requested_at = db.Column(db.DateTime, default=_now)
    fulfilled_by_contact_id = db.Column(db.Integer, db.ForeignKey("client_contacts.id"))
    fulfilled_at = db.Column(db.DateTime)
    received_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    received_at = db.Column(db.DateTime)
    rejected_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    rejected_at = db.Column(db.DateTime)
    rejection_comment = db.Column(db.Text)

    requested_by = db.relationship("User", foreign_keys=[requested_by_id])
    fulfilled_by_contact = db.relationship("ClientContact")
    received_by = db.relationship("User", foreign_keys=[received_by_id])
    rejected_by = db.relationship("User", foreign_keys=[rejected_by_id])
    response_document = db.relationship("Document", foreign_keys=[response_document_id])


class Document(db.Model):
    __tablename__ = "documents"

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False)
    conversation_id = db.Column(db.Integer, db.ForeignKey("conversations.id"))
    milestone_id = db.Column(db.Integer, db.ForeignKey("milestones.id"))

    file_name = db.Column(db.String(300), nullable=False)
    stored_name = db.Column(db.String(300), nullable=False)
    file_type = db.Column(db.String(40))
    file_size = db.Column(db.Integer)
    uploaded_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    # Set instead of uploaded_by_id when a client contact uploads a file
    # through the portal (responding to a request) rather than staff
    # attaching a deliverable.
    uploaded_by_contact_id = db.Column(db.Integer, db.ForeignKey("client_contacts.id"))
    uploaded_at = db.Column(db.DateTime, default=_now)

    uploaded_by = db.relationship("User")
    uploaded_by_contact = db.relationship("ClientContact")

    @property
    def uploader_name(self):
        if self.uploaded_by_contact:
            return self.uploaded_by_contact.name
        if self.uploaded_by:
            return self.uploaded_by.name
        return "Unknown"

    @property
    def uploader_role(self):
        return "Client" if self.uploaded_by_contact_id else "Scaalex"

    @property
    def context_label(self):
        """Human-readable reason this document exists, for the Documents
        tab - a client's response to a specific request, a staff-attached
        deliverable, a conversation attachment, or a plain manual upload."""
        if self.milestone_id and self.milestone:
            request = MilestoneRequest.query.filter_by(response_document_id=self.id).first()
            if request:
                return f'Response to request on "{self.milestone.title}": {request.message}'
            return f"Deliverable for milestone: {self.milestone.title}"
        if self.conversation_id and self.conversation:
            c = self.conversation
            return f"Attached to update: {c.interaction_type} - {c.date.strftime('%d %b %Y')}"
        return "Manual upload"


class AIOverview(db.Model):
    __tablename__ = "ai_overviews"

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False)
    generated_at = db.Column(db.DateTime, default=_now)

    # Optional date range the overview was scoped to - null on either end
    # means unbounded (full history on that side).
    period_start = db.Column(db.Date)
    period_end = db.Column(db.Date)

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


class FirefliesMeeting(db.Model):
    """A meeting pulled from Fireflies, staged before it's linked to a client.

    Sync (see ai/fireflies_client.py + routes/fireflies.py) attempts to
    auto-match each meeting to a client by name; a confident match creates
    the Conversation immediately (status="assigned"). Anything that can't
    be matched lands here with status="uncategorized" for an advisor to
    assign manually from the Fireflies inbox page.
    """

    __tablename__ = "fireflies_meetings"

    id = db.Column(db.Integer, primary_key=True)
    fireflies_id = db.Column(db.String(120), unique=True, nullable=False)
    title = db.Column(db.String(300))
    meeting_date = db.Column(db.DateTime)
    duration_minutes = db.Column(db.Float)
    participants = db.Column(JSONText, default=list)
    transcript = db.Column(db.Text)
    fireflies_overview = db.Column(db.Text)  # Fireflies' own summary - a hint only, never treated as our record

    status = db.Column(db.String(20), default="uncategorized")  # uncategorized, assigned, ignored
    matched_client_id = db.Column(db.Integer, db.ForeignKey("clients.id"))  # auto-match suggestion
    assigned_client_id = db.Column(db.Integer, db.ForeignKey("clients.id"))
    assigned_conversation_id = db.Column(db.Integer, db.ForeignKey("conversations.id"))
    synced_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))  # who ran the sync/assign that completed this

    synced_at = db.Column(db.DateTime, default=_now)

    matched_client = db.relationship("Client", foreign_keys=[matched_client_id])
    assigned_client = db.relationship("Client", foreign_keys=[assigned_client_id])
    assigned_conversation = db.relationship("Conversation")
    synced_by = db.relationship("User", foreign_keys=[synced_by_id])


class CalendarConnection(db.Model):
    """A connected Google Calendar account, used to show upcoming meetings.

    This is a single shared connection for the firm (whoever connects it),
    not per-advisor - matching how Fireflies sync also runs against one
    shared workspace rather than per-user credentials.
    """

    __tablename__ = "calendar_connections"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(200))
    access_token = db.Column(db.Text, nullable=False)
    refresh_token = db.Column(db.Text, nullable=False)
    token_expiry = db.Column(db.DateTime, nullable=False)
    connected_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    connected_at = db.Column(db.DateTime, default=_now)

    connected_by = db.relationship("User")


class AuditLog(db.Model):
    __tablename__ = "audit_log"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    # Set instead of user_id when the actor is a client contact on the
    # portal (a sign-in, upload, or download), so the Logs tab can show one
    # merged timeline of both staff and client activity.
    client_contact_id = db.Column(db.Integer, db.ForeignKey("client_contacts.id"))
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"))
    action = db.Column(db.String(120), nullable=False)
    entity_type = db.Column(db.String(60))
    entity_id = db.Column(db.Integer)
    details = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=_now)

    user = db.relationship("User")
    client_contact = db.relationship("ClientContact")


def log_activity(user_id, client_id, action, entity_type=None, entity_id=None, details=None):
    entry = AuditLog(
        user_id=user_id, client_id=client_id, action=action,
        entity_type=entity_type, entity_id=entity_id, details=details,
    )
    db.session.add(entry)


def log_portal_activity(client_contact_id, client_id, action, entity_type=None, entity_id=None, details=None):
    entry = AuditLog(
        client_contact_id=client_contact_id, client_id=client_id, action=action,
        entity_type=entity_type, entity_id=entity_id, details=details,
    )
    db.session.add(entry)
