import os

from flask import Flask, render_template, flash, redirect, request
from flask_login import current_user
from flask_wtf.csrf import CSRFError
from werkzeug.middleware.proxy_fix import ProxyFix

from config import Config, DATA_DIR
from extensions import db, login_manager, csrf, limiter


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    # Render (and most PaaS hosts) terminate TLS in front of the app and
    # forward plain HTTP with X-Forwarded-* headers. Without this, Flask
    # thinks every request is http://, which breaks secure cookies and any
    # https-aware redirect.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    os.makedirs(os.path.join(DATA_DIR, "instance"), exist_ok=True)
    os.makedirs(app.config["DOCUMENT_UPLOAD_FOLDER"], exist_ok=True)

    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)
    limiter.init_app(app)

    from models import User

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    from auth import auth_bp, DEACTIVATED_MESSAGE
    from routes.home import home_bp
    from routes.clients import clients_bp
    from routes.conversations import conversations_bp
    from routes.decisions import decisions_bp
    from routes.action_items import action_items_bp
    from routes.documents import documents_bp
    from routes.ai_overview import ai_overview_bp
    from routes.search import search_bp
    from routes.ai_api import ai_api_bp
    from routes.fireflies import fireflies_bp
    from routes.calendar import calendar_bp
    from routes.reports import reports_bp
    from routes.calendar_board import calendar_board_bp
    from routes.portal import portal_bp, portal_hub_bp
    from routes.notify import notify_bp
    from routes.data_room import data_room_bp

    @app.before_request
    def end_deactivated_sessions():
        # Deactivating someone takes effect on their next click, not when
        # their session cookie happens to expire.
        from flask import flash, redirect, request, url_for
        from flask_login import current_user, logout_user
        if request.endpoint in (None, "static") or not current_user.is_authenticated:
            return None
        if not current_user.is_active:
            logout_user()
            flash(DEACTIVATED_MESSAGE, "error")
            return redirect(url_for("auth.login"))
        return None

    app.register_blueprint(auth_bp)
    app.register_blueprint(home_bp)
    app.register_blueprint(clients_bp)
    app.register_blueprint(conversations_bp)
    app.register_blueprint(decisions_bp)
    app.register_blueprint(action_items_bp)
    app.register_blueprint(documents_bp)
    app.register_blueprint(ai_overview_bp)
    app.register_blueprint(search_bp)
    app.register_blueprint(ai_api_bp)
    app.register_blueprint(fireflies_bp)
    app.register_blueprint(calendar_bp)
    app.register_blueprint(reports_bp)
    app.register_blueprint(calendar_board_bp)
    app.register_blueprint(portal_bp)
    app.register_blueprint(portal_hub_bp)
    app.register_blueprint(notify_bp)
    app.register_blueprint(data_room_bp)

    from brand import BRAND

    @app.context_processor
    def inject_brand():
        return {"brand": BRAND}

    @app.context_processor
    def inject_globals():
        from flask import request as _request
        from models import Client, FirefliesMeeting, Conversation

        if not current_user.is_authenticated:
            return {"sidebar_clients": [], "fireflies_uncategorized_count": 0, "sync_pending_count": 0}

        sidebar_clients = Client.query.order_by(Client.name).all()
        if not current_user.is_admin:
            sidebar_clients = [c for c in sidebar_clients if current_user.can_view_client(c.id)]
        uncategorized_count = FirefliesMeeting.query.filter_by(status="uncategorized").count()

        sync_pending_count = 0
        client_slug = (_request.view_args or {}).get("slug")
        if client_slug:
            client_for_count = next((c for c in sidebar_clients if c.slug == client_slug), None)
            if client_for_count:
                pending = Conversation.query.filter_by(
                    client_id=client_for_count.id, extraction_status="none",
                ).all()
                sync_pending_count = sum(1 for c in pending if (c.raw_notes or c.transcript or "").strip())

        return {
            "sidebar_clients": sidebar_clients,
            "fireflies_uncategorized_count": uncategorized_count,
            "sync_pending_count": sync_pending_count,
        }

    @app.errorhandler(CSRFError)
    def csrf_error(e):
        flash("Your session security token expired or was invalid. Please try again.", "error")
        return redirect(request.referrer or "/"), 302

    @app.errorhandler(403)
    def forbidden(e):
        return render_template("error.html", code=403, message="You don't have access to this."), 403

    @app.errorhandler(404)
    def not_found(e):
        return render_template("error.html", code=404, message="Page not found."), 404

    @app.errorhandler(413)
    def too_large(e):
        return render_template("error.html", code=413, message="File is too large to upload."), 413

    @app.errorhandler(500)
    def server_error(e):
        return render_template("error.html", code=500, message="Something went wrong."), 500

    with app.app_context():
        db.create_all()
        _ensure_schema_migrations(app)

    import backup
    backup.start_scheduler(app)

    return app


def _ensure_schema_migrations(app):
    """db.create_all() only creates missing tables, never alters existing
    ones. This is a small SQLite app without Alembic, so additive column
    changes are applied by hand here rather than leaving them to crash at
    query time on an already-deployed database."""
    if not app.config["SQLALCHEMY_DATABASE_URI"].startswith("sqlite"):
        return

    with db.engine.connect() as conn:
        existing_columns = {row[1] for row in conn.execute(db.text("PRAGMA table_info(conversations)"))}
        if "source" not in existing_columns:
            conn.execute(db.text("ALTER TABLE conversations ADD COLUMN source VARCHAR(20) DEFAULT 'manual'"))
            conn.commit()

        # Voice upload/Whisper transcription was removed in favor of Fireflies
        # sync; drop the columns that only existed for that flow. DROP COLUMN
        # needs SQLite 3.35+ - skip quietly on anything older rather than
        # crashing app startup over two now-harmless unused columns.
        for column in ("audio_filename", "audio_original_name"):
            if column in existing_columns:
                try:
                    conn.execute(db.text(f"ALTER TABLE conversations DROP COLUMN {column}"))
                    conn.commit()
                except Exception:
                    conn.rollback()

        meeting_columns = {row[1] for row in conn.execute(db.text("PRAGMA table_info(fireflies_meetings)"))}
        if "synced_by_id" not in meeting_columns:
            conn.execute(db.text("ALTER TABLE fireflies_meetings ADD COLUMN synced_by_id INTEGER"))
            conn.commit()

        overview_columns = {row[1] for row in conn.execute(db.text("PRAGMA table_info(ai_overviews)"))}
        for column in ("period_start", "period_end"):
            if column not in overview_columns:
                conn.execute(db.text(f"ALTER TABLE ai_overviews ADD COLUMN {column} DATE"))
                conn.commit()

        cal_columns = {row[1] for row in conn.execute(db.text("PRAGMA table_info(calendar_connections)"))}
        if cal_columns and "scopes" not in cal_columns:
            conn.execute(db.text("ALTER TABLE calendar_connections ADD COLUMN scopes TEXT"))
            conn.commit()

        user_columns = {row[1] for row in conn.execute(db.text("PRAGMA table_info(users)"))}
        for column, ddl_type in (
            ("last_login_at", "DATETIME"), ("deactivated_at", "DATETIME"), ("deactivated_by_id", "INTEGER"),
        ):
            if column not in user_columns:
                conn.execute(db.text(f"ALTER TABLE users ADD COLUMN {column} {ddl_type}"))
                conn.commit()

        client_columns = {row[1] for row in conn.execute(db.text("PRAGMA table_info(clients)"))}
        for column, ddl_type in (
            ("registered_name", "VARCHAR(300)"), ("address", "TEXT"),
            ("website", "VARCHAR(300)"), ("gst_number", "VARCHAR(40)"),
            ("portal_slug", "VARCHAR(200)"), ("data_room_enabled", "BOOLEAN DEFAULT 0"),
        ):
            if column not in client_columns:
                conn.execute(db.text(f"ALTER TABLE clients ADD COLUMN {column} {ddl_type}"))
                conn.commit()

        # Billing was removed in favor of Client Details only; drop the
        # columns it briefly added. Same SQLite 3.35+ caveat as above.
        for column in (
            "billing_contact", "billing_email", "payment_terms",
            "invoice_currency", "billing_address",
        ):
            if column in client_columns:
                try:
                    conn.execute(db.text(f"ALTER TABLE clients DROP COLUMN {column}"))
                    conn.commit()
                except Exception:
                    conn.rollback()

        contact_columns = {row[1] for row in conn.execute(db.text("PRAGMA table_info(client_contacts)"))}
        for column, ddl_type in (
            ("portal_access", "BOOLEAN DEFAULT 0"), ("password_hash", "VARCHAR(255)"),
            ("must_change_password", "BOOLEAN DEFAULT 1"), ("last_login_at", "DATETIME"),
            ("terms_accepted_at", "DATETIME"), ("data_room_access", "BOOLEAN DEFAULT 0"),
        ):
            if column not in contact_columns:
                conn.execute(db.text(f"ALTER TABLE client_contacts ADD COLUMN {column} {ddl_type}"))
                conn.commit()

        document_columns = {row[1] for row in conn.execute(db.text("PRAGMA table_info(documents)"))}
        for column, ddl_type in (
            ("milestone_id", "INTEGER"), ("uploaded_by_contact_id", "INTEGER"), ("folder_id", "INTEGER"),
        ):
            if column not in document_columns:
                conn.execute(db.text(f"ALTER TABLE documents ADD COLUMN {column} {ddl_type}"))
                conn.commit()

        audit_columns = {row[1] for row in conn.execute(db.text("PRAGMA table_info(audit_log)"))}
        if "client_contact_id" not in audit_columns:
            conn.execute(db.text("ALTER TABLE audit_log ADD COLUMN client_contact_id INTEGER"))
            conn.commit()

        request_columns = {row[1] for row in conn.execute(db.text("PRAGMA table_info(milestone_requests)"))}
        for column, ddl_type in (
            ("received_by_id", "INTEGER"), ("received_at", "DATETIME"),
            ("rejected_by_id", "INTEGER"), ("rejected_at", "DATETIME"),
            ("rejection_comment", "TEXT"),
        ):
            if column not in request_columns:
                conn.execute(db.text(f"ALTER TABLE milestone_requests ADD COLUMN {column} {ddl_type}"))
                conn.commit()

        action_item_columns = {row[1] for row in conn.execute(db.text("PRAGMA table_info(action_items)"))}
        for column, ddl_type in (("assignee", "VARCHAR(200)"), ("notes", "TEXT")):
            if column not in action_item_columns:
                conn.execute(db.text(f"ALTER TABLE action_items ADD COLUMN {column} {ddl_type}"))
                conn.commit()

        # One-time role backfill for accounts that predate the Owner/
        # Administrator/Executive system - everyone else defaults to
        # "executive" (see User.role's column default) with no client
        # access until an Admin grants it.
        for email, role in (
            ("anand@scaalex.com", "owner"),
            ("sarfaraz@scaalex.com", "admin"),
            ("sujith@scaalex.com", "admin"),
        ):
            conn.execute(
                db.text(
                    "UPDATE users SET role = :role WHERE email = :email "
                    "AND (role IS NULL OR role NOT IN ('owner', 'admin', 'executive'))"
                ),
                {"role": role, "email": email},
            )
            conn.commit()


app = create_app()

if __name__ == "__main__":
    app.run(debug=True, port=5050)
