import os
from dotenv import load_dotenv

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

# DATA_DIR is where the SQLite file and uploaded files live. Locally this is
# just the project directory. On a host with a persistent disk (e.g. Render),
# set DATA_DIR to that disk's mount path so the database and uploads survive
# deploys/restarts - everything that needs to persist lives under one path.
DATA_DIR = os.environ.get("DATA_DIR", BASE_DIR).strip() or BASE_DIR


def _normalize_db_url(url: str) -> str:
    # Render (and some other hosts) hand out postgres:// URLs; SQLAlchemy 1.4+
    # requires the postgresql:// scheme.
    if url.startswith("postgres://"):
        return "postgresql://" + url[len("postgres://"):]
    return url


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-insecure-key")
    SQLALCHEMY_DATABASE_URI = _normalize_db_url(os.environ.get("DATABASE_URL", "").strip()) or (
        f"sqlite:///{os.path.join(DATA_DIR, 'instance', 'scaalex.db')}"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # SQLite connections are single-thread by default, but gunicorn's
    # --threads setting runs multiple request threads inside one process.
    # Without this, a connection created on one thread gets handed to
    # another thread by SQLAlchemy's pool and raises
    # "SQLite objects created in a thread can only be used in that same
    # thread" - which crashes the worker and shows up as an intermittent
    # 502 at the proxy. This only applies to SQLite; Postgres doesn't need it.
    if SQLALCHEMY_DATABASE_URI.startswith("sqlite"):
        SQLALCHEMY_ENGINE_OPTIONS = {"connect_args": {"check_same_thread": False}}

    ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")

    FIREFLIES_API_KEY = os.environ.get("FIREFLIES_API_KEY", "").strip()

    GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
    GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()

    # Daily off-host backup (database + uploaded documents) to Cloudflare R2.
    # Backups are skipped entirely - no error, just a log line - when these
    # aren't set, so local dev and any deploy that hasn't configured R2 yet
    # both work fine without them.
    R2_ACCOUNT_ID = os.environ.get("R2_ACCOUNT_ID", "").strip()
    R2_ACCESS_KEY_ID = os.environ.get("R2_ACCESS_KEY_ID", "").strip()
    R2_SECRET_ACCESS_KEY = os.environ.get("R2_SECRET_ACCESS_KEY", "").strip()
    R2_BUCKET_NAME = os.environ.get("R2_BUCKET_NAME", "").strip()
    R2_ENDPOINT_URL = os.environ.get("R2_ENDPOINT_URL", "").strip() or (
        f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com" if R2_ACCOUNT_ID else ""
    )

    UPLOAD_FOLDER = os.path.join(DATA_DIR, "uploads")
    DOCUMENT_UPLOAD_FOLDER = os.path.join(UPLOAD_FOLDER, "documents")
    MAX_CONTENT_LENGTH = int(os.environ.get("MAX_UPLOAD_MB", "50")) * 1024 * 1024

    ALLOWED_DOCUMENT_EXTENSIONS = {
        "pdf", "ppt", "pptx", "xls", "xlsx", "doc", "docx",
        "png", "jpg", "jpeg", "gif", "txt", "csv",
    }

    # Cookie hardening. SESSION_COOKIE_SECURE is only safe once the app is
    # actually served over HTTPS (Render terminates TLS in front of the app
    # and sets X-Forwarded-Proto - see ProxyFix in app.py). Locally over
    # plain http:// a "secure" cookie would never be sent back, so this is
    # opt-in via env rather than always-on.
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.environ.get("FORCE_HTTPS", "").strip().lower() in ("1", "true", "yes")
