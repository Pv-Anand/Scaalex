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

    from auth import auth_bp
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

    @app.context_processor
    def inject_globals():
        from models import Client, FirefliesMeeting
        if not current_user.is_authenticated:
            return {"sidebar_clients": [], "fireflies_uncategorized_count": 0}
        sidebar_clients = Client.query.order_by(Client.name).all()
        uncategorized_count = FirefliesMeeting.query.filter_by(status="uncategorized").count()
        return {"sidebar_clients": sidebar_clients, "fireflies_uncategorized_count": uncategorized_count}

    @app.errorhandler(CSRFError)
    def csrf_error(e):
        flash("Your session security token expired or was invalid. Please try again.", "error")
        return redirect(request.referrer or "/"), 302

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


app = create_app()

if __name__ == "__main__":
    app.run(debug=True, port=5050)
