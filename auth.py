from datetime import datetime

from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_user, logout_user, login_required, current_user

from extensions import db, limiter
from models import User

auth_bp = Blueprint("auth", __name__)


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
def team():
    users = User.query.order_by(User.name).all()
    return render_template("team.html", users=users)


@auth_bp.route("/team/<int:user_id>/reset-password", methods=["POST"])
@login_required
def reset_password(user_id):
    user = User.query.get_or_404(user_id)
    new_password = request.form.get("new_password", "")
    if len(new_password) < 8:
        flash("Password must be at least 8 characters.", "error")
        return redirect(url_for("auth.team"))
    user.set_password(new_password)
    db.session.commit()
    flash(f"Password updated for {user.name}.", "success")
    return redirect(url_for("auth.team"))
