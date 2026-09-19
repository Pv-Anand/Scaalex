"""Daily off-host backup: the SQLite database (via VACUUM INTO, a clean
atomic snapshot even while the app is writing) plus the uploaded documents
folder, both shipped to Cloudflare R2.

Runs on a background thread inside the single gunicorn worker (see
render.yaml: --workers 1) rather than a separate Render service or cron
job, since Render's persistent disk can only be mounted by one service -
this process is the only one with filesystem access to DATA_DIR.
"""
import json
import logging
import os
import sqlite3
import tarfile
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone

logger = logging.getLogger("backup")

BACKUP_HOUR_UTC = 2  # once a day at 02:00 UTC, off peak for an IST-based team
RETENTION_DAYS = 30  # daily backups older than this are pruned, except...
KEEP_MONTHLY = True  # ...the first-of-month one, kept indefinitely as an archive


def _is_configured(app):
    cfg = app.config
    return bool(
        cfg.get("R2_ACCESS_KEY_ID") and cfg.get("R2_SECRET_ACCESS_KEY")
        and cfg.get("R2_BUCKET_NAME") and cfg.get("R2_ENDPOINT_URL")
    )


def _r2_client(app):
    import boto3
    return boto3.client(
        "s3",
        endpoint_url=app.config["R2_ENDPOINT_URL"],
        aws_access_key_id=app.config["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=app.config["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
    )


def _status_path(app):
    from config import DATA_DIR
    return os.path.join(DATA_DIR, "backup_status.json")


def _write_status(app, **fields):
    path = _status_path(app)
    data = {}
    if os.path.exists(path):
        try:
            with open(path) as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            data = {}
    data.update(fields)
    try:
        with open(path, "w") as f:
            json.dump(data, f)
    except OSError:
        logger.exception("Could not write backup status file")


def read_status(app):
    path = _status_path(app)
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def run_backup(app):
    """Snapshot the DB and documents, upload both to R2, then prune backups
    older than RETENTION_DAYS (keeping one per month indefinitely)."""
    if not _is_configured(app):
        logger.info("Backup skipped - R2 isn't configured (R2_* env vars not set).")
        return False

    today = datetime.now(timezone.utc).date()
    stamp = today.isoformat()

    try:
        client = _r2_client(app)
        bucket = app.config["R2_BUCKET_NAME"]

        db_uri = app.config["SQLALCHEMY_DATABASE_URI"]
        if not db_uri.startswith("sqlite"):
            raise RuntimeError("This backup script only knows how to snapshot SQLite.")
        db_path = db_uri.replace("sqlite:///", "", 1)

        with tempfile.TemporaryDirectory() as tmp:
            db_backup_path = os.path.join(tmp, "db.sqlite3")
            conn = sqlite3.connect(db_path)
            conn.execute("VACUUM INTO ?", (db_backup_path,))
            conn.close()
            client.upload_file(db_backup_path, bucket, f"backups/db/{stamp}.sqlite3")
            db_size = os.path.getsize(db_backup_path)

            docs_dir = app.config["DOCUMENT_UPLOAD_FOLDER"]
            docs_backup_path = os.path.join(tmp, "documents.tar.gz")
            with tarfile.open(docs_backup_path, "w:gz") as tar:
                if os.path.isdir(docs_dir):
                    tar.add(docs_dir, arcname="documents")
            client.upload_file(docs_backup_path, bucket, f"backups/documents/{stamp}.tar.gz")
            docs_size = os.path.getsize(docs_backup_path)

        pruned = _prune_old_backups(client, bucket, today)
        _write_status(
            app, last_run_at=datetime.now(timezone.utc).isoformat(), last_status="ok",
            last_error=None, db_bytes=db_size, documents_bytes=docs_size, pruned=pruned,
        )
        logger.info("Backup for %s uploaded to R2 (db=%d bytes, documents=%d bytes).", stamp, db_size, docs_size)
        return True
    except Exception as exc:
        logger.exception("Backup failed")
        _write_status(app, last_run_at=datetime.now(timezone.utc).isoformat(), last_status="error", last_error=str(exc))
        return False


def _prune_old_backups(client, bucket, today):
    cutoff = today - timedelta(days=RETENTION_DAYS)
    deleted = 0
    for prefix in ("backups/db/", "backups/documents/"):
        paginator = client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                name = key.rsplit("/", 1)[-1]
                date_str = name.split(".")[0]
                try:
                    obj_date = datetime.strptime(date_str, "%Y-%m-%d").date()
                except ValueError:
                    continue
                if obj_date >= cutoff:
                    continue
                if KEEP_MONTHLY and obj_date.day == 1:
                    continue
                client.delete_object(Bucket=bucket, Key=key)
                deleted += 1
    return deleted


def start_scheduler(app):
    if not _is_configured(app):
        logger.info("Backup scheduler not started - R2 isn't configured.")
        return

    def loop():
        while True:
            now = datetime.now(timezone.utc)
            next_run = now.replace(hour=BACKUP_HOUR_UTC, minute=0, second=0, microsecond=0)
            if next_run <= now:
                next_run += timedelta(days=1)
            time.sleep((next_run - now).total_seconds())
            with app.app_context():
                run_backup(app)

    threading.Thread(target=loop, daemon=True, name="backup-scheduler").start()
    logger.info("Backup scheduler started - daily at %02d:00 UTC.", BACKUP_HOUR_UTC)
