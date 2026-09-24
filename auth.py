from datetime import datetime

from flask import Blueprint, render_template, redirect, url_for, request, flash, current_app
from flask_login import login_user, logout_user, login_required, current_user

import backup
from extensions import db, limiter
from models import User, Client, ClientAccess, ActionItem, ROLES, ROLE_LABELS, log_activity
from permissions import admin_required

auth_bp = Blueprint("auth", __name__)

DEACTIVATED_MESSAGE = "Your Scaalex account has been deactivated. Please contact your Scaalex administrator."


@auth_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute", methods=["POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("home.index"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = User.query.filter_by(email=email).first()

        if user and user.check_password(password):
            # Only said after the right password, so it never reveals which
            # emails exist.
            if not user.is_active:
                flash(DEACTIVATED_MESSAGE, "error")
                return render_template("login.html", current_year=datetime.utcnow().year)
            user.last_login_at = datetime.utcnow()
            db.session.commit()
            login_user(user)
            next_url = request.args.get("next")
            return redirect(next_url or url_for("home.index"))

        flash("Invalid email or password.", "error")

    return render_template("login.html", current_year=datetime.utcnow().year)


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))


@auth_bp.route("/account", methods=["GET", "POST"])
@login_required
def account():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if not name:
            flash("Name can't be empty.", "error")
            return redirect(url_for("auth.account"))
        current_user.name = name
        db.session.commit()
        flash("Profile updated.", "success")
        return redirect(url_for("auth.account"))

    return render_template("account.html")


@auth_bp.route("/team")
@login_required
@admin_required
def team():
    users = User.query.order_by(User.name).all()
    clients = Client.query.order_by(Client.name).all()

    access_map = {}
    for grant in ClientAccess.query.all():
        access_map[(grant.user_id, grant.client_id)] = grant.access_level

    # Open action items assigned to each person (assignee is free text that
    # holds the person's name), so the deactivate confirm can offer to hand
    # them over.
    open_items = {}
    for u in users:
        rows = ActionItem.query.filter(
            ActionItem.assignee == u.name, ActionItem.status != "Completed",
        ).all()
        open_items[u.id] = {"count": len(rows), "clients": len({r.client_id for r in rows})}

    return render_template(
        "team.html", users=users, clients=clients, access_map=access_map, open_items=open_items,
        roles=ROLES, role_labels=ROLE_LABELS,
        backup_status=backup.read_status(current_app._get_current_object()),
        backup_configured=backup._is_configured(current_app._get_current_object()),
    )


@auth_bp.route("/team/backup", methods=["POST"])
@login_required
@admin_required
def run_backup_now():
    app_obj = current_app._get_current_object()
    if not backup._is_configured(app_obj):
        flash("Backups aren't configured yet - set the R2_* environment variables first.", "error")
        return redirect(url_for("auth.team"))

    ok = backup.run_backup(app_obj)
    if ok:
        log_activity(current_user.id, None, "Backup run manually", "backup")
        db.session.commit()
        flash("Backup completed and uploaded to R2.", "success")
    else:
        status = backup.read_status(app_obj) or {}
        flash(f"Backup failed: {status.get('last_error', 'unknown error')}", "error")
    return redirect(url_for("auth.team"))


@auth_bp.route("/team/new", methods=["POST"])
@login_required
@admin_required
def add_user():
    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip().lower()
    role = request.form.get("role", "executive")
    password = request.form.get("password", "")

    if not name or not email:
        flash("Name and email are required.", "error")
        return redirect(url_for("auth.team"))
    if role not in ROLES:
        role = "executive"
    if role == "owner" and not current_user.is_owner:
        flash("Only the Owner can create another Owner account.", "error")
        return redirect(url_for("auth.team"))
    if len(password) < 8:
        flash("Initial password must be at least 8 characters.", "error")
        return redirect(url_for("auth.team"))
    if User.query.filter_by(email=email).first():
        flash(f"{email} is already in use.", "error")
        return redirect(url_for("auth.team"))

    user = User(name=name, email=email, role=role)
    user.set_password(password)
    db.session.add(user)
    db.session.flush()
    log_activity(current_user.id, None, "User created", "user", user.id, details=f"{name} ({role})")
    db.session.commit()
    flash(f"{name} added as {user.role_label}.", "success")
    return redirect(url_for("auth.team"))


@auth_bp.route("/team/<int:user_id>/reset-password", methods=["POST"])
@login_required
@admin_required
def reset_password(user_id):
    user = User.query.get_or_404(user_id)
    new_password = request.form.get("new_password", "")
    if len(new_password) < 8:
        flash("Password must be at least 8 characters.", "error")
        return redirect(url_for("auth.team"))
    user.set_password(new_password)
    log_activity(current_user.id, None, "Password reset", "user", user.id, details=user.name)
    db.session.commit()
    flash(f"Password updated for {user.name}.", "success")
    return redirect(url_for("auth.team"))


@auth_bp.route("/team/<int:user_id>/role", methods=["POST"])
@login_required
@admin_required
def update_role(user_id):
    user = User.query.get_or_404(user_id)
    new_role = request.form.get("role", "")

    if user.id == current_user.id:
        flash("You can't change your own role.", "error")
        return redirect(url_for("auth.team"))
    if new_role not in ROLES:
        flash("Not a valid role.", "error")
        return redirect(url_for("auth.team"))
    if (new_role == "owner" or user.role == "owner") and not current_user.is_owner:
        flash("Only the Owner can assign the Owner role or change another Owner.", "error")
        return redirect(url_for("auth.team"))

    user.role = new_role
    log_activity(current_user.id, None, "Role changed", "user", user.id, details=f"{user.name} → {user.role_label}")
    db.session.commit()
    flash(f"{user.name} is now {user.role_label}.", "success")
    return redirect(url_for("auth.team"))


@auth_bp.route("/team/<int:user_id>/access", methods=["POST"])
@login_required
@admin_required
def update_access(user_id):
    user = User.query.get_or_404(user_id)
    clients = Client.query.all()

    for client in clients:
        level = request.form.get(f"access_{client.id}", "none")
        grant = ClientAccess.query.filter_by(user_id=user.id, client_id=client.id).first()
        if level in ("view", "edit"):
            if grant:
                grant.access_level = level
            else:
                db.session.add(ClientAccess(user_id=user.id, client_id=client.id, access_level=level))
        elif grant:
            db.session.delete(grant)

    log_activity(current_user.id, None, "Client access updated", "user", user.id, details=user.name)
    db.session.commit()
    flash(f"Client access updated for {user.name}.", "success")
    return redirect(url_for("auth.team"))


def _can_change_access(target):
    """Returns an error message if current_user may not activate/deactivate
    `target`, else None."""
    if target.id == current_user.id:
        return "You can't deactivate your own account."
    if target.role == "owner" and not current_user.is_owner:
        return "Only an Owner can change another Owner's access."
    return None


@auth_bp.route("/team/<int:user_id>/deactivate", methods=["POST"])
@login_required
@admin_required
def deactivate_user(user_id):
    user = User.query.get_or_404(user_id)
    error = _can_change_access(user)
    if error:
        flash(error, "error")
        return redirect(url_for("auth.team"))
    if user.role == "owner":
        active_owners = User.query.filter(User.role == "owner", User.deactivated_at.is_(None)).count()
        if active_owners <= 1:
            flash("The last active Owner can't be deactivated.", "error")
            return redirect(url_for("auth.team"))
    if not user.is_active:
        return redirect(url_for("auth.team"))

    moved = 0
    target_id = request.form.get("reassign_to", "").strip()
    new_owner = User.query.get(int(target_id)) if target_id.isdigit() else None
    if new_owner and (new_owner.id == user.id or not new_owner.is_active):
        new_owner = None
    if new_owner:
        items = ActionItem.query.filter(ActionItem.assignee == user.name, ActionItem.status != "Completed").all()
        for item in items:
            item.assignee = new_owner.name
        moved = len(items)

    user.deactivated_at = datetime.utcnow()
    user.deactivated_by_id = current_user.id
    details = user.name if not moved else f"{user.name} ({moved} open items reassigned to {new_owner.name})"
    log_activity(current_user.id, None, "Team member deactivated", "user", user.id, details=details)
    db.session.commit()
    extra = f", {moved} open item{'s' if moved != 1 else ''} reassigned to {new_owner.name}" if moved else ""
    flash(f"{user.name} deactivated{extra}.", "success")
    return redirect(url_for("auth.team"))


@auth_bp.route("/team/<int:user_id>/reactivate", methods=["POST"])
@login_required
@admin_required
def reactivate_user(user_id):
    user = User.query.get_or_404(user_id)
    error = _can_change_access(user)
    if error:
        flash(error, "error")
        return redirect(url_for("auth.team"))
    user.deactivated_at = None
    user.deactivated_by_id = None
    log_activity(current_user.id, None, "Team member reactivated", "user", user.id, details=user.name)
    db.session.commit()
    flash(f"{user.name} reactivated.", "success")
    return redirect(url_for("auth.team"))
