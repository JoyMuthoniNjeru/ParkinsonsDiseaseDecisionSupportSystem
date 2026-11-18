from flask import render_template, request, redirect, url_for, flash
from flask_login import login_user, logout_user, login_required, current_user
from app.auth import bp
from app.models import Patient, Caregiver, HealthcareProfessional, Admin
from app.extensions import db
from app.validators import password_error
from app.mail import send_email_in_background
from app import password_reset

# The public log in is for the people who use the app. The Admin is a back-office user with
# their own log in page (/admin/login, not linked anywhere), so "admin" is not accepted here.
ROLE_MODELS = {
    "patient": Patient,
    "caregiver": Caregiver,
    "hcp": HealthcareProfessional,
}


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        role = request.form.get("role")
        email = request.form.get("email")
        password = request.form.get("password")

        model = ROLE_MODELS.get(role)
        if model is None:
            flash("Invalid role selected.", "error")
            return redirect(url_for("auth.login"))

        user = model.query.filter_by(email=email).first()
        if user is None or not user.check_password(password):
            flash("Incorrect email or password.", "error")
            return redirect(url_for("auth.login"))

        if role == "hcp" and user.credential_status != "approved":
            flash(f"Your HCP application is currently {user.credential_status}. "
                  "You cannot log in until an Admin approves it.")
            return redirect(url_for("auth.login"))

        if hasattr(user, "account_status") and user.account_status != "active":
            flash("This account has been deactivated. Contact an Admin.", "error")
            return redirect(url_for("auth.login"))

        login_user(user)
        return redirect(url_for(f"{role}.dashboard"))

    return render_template("auth/login.html")


# ---------------------------------------------------------------------------
# Forgot password -> reset link by email (18 Nov). Logic in app/password_reset.py.
# ---------------------------------------------------------------------------

@bp.route("/forgot", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        role = request.form.get("role")
        if role not in password_reset.ROLE_MODELS:
            flash("Please choose Patient, Caregiver or Clinician.", "error")
            return render_template("auth/forgot.html", values=request.form)
        user = password_reset.find_user(role, request.form.get("email"))
        if user is not None and password_reset.can_reset(user):
            subject, body = password_reset.reset_email(role, user)
            send_email_in_background(user.email, subject, body)
        # The SAME answer whether or not the account exists, so this page can't be used to
        # find out who has an account (privacy). Sending happens in the background, so it's
        # also just as fast either way.
        flash(f"If there is a {password_reset.ROLE_LABELS[role]} account with that email, we've sent "
              f"it a link to choose a new password. It works for {password_reset.RESET_LINK_MINUTES} "
              "minutes. Check your spam folder if you can't see it.", "success")
        return redirect(url_for("auth.login"))
    return render_template("auth/forgot.html", values={})


@bp.route("/reset/<token>", methods=["GET", "POST"])
def reset_password(token):
    role, user = password_reset.user_from_token(token)
    if user is None:
        flash("This reset link has expired or has already been used. Please ask for a new one.", "warning")
        return redirect(url_for("auth.forgot_password"))
    if request.method == "POST":
        error = password_error(request.form.get("password", ""), request.form.get("confirm_password", ""))
        if error:
            flash(error, "error")
            return render_template("auth/reset_password.html", token=token, user=user)
        user.set_password(request.form["password"])   # also makes this link (and older ones) stop working
        db.session.commit()
        flash("Your password has been changed. You can log in with it now.", "success")
        return redirect(url_for("auth.login"))
    return render_template("auth/reset_password.html", token=token, user=user)


@bp.route("/password", methods=["GET", "POST"])
@login_required
def change_password():
    """Change password while logged in (every role, incl. Admin). Needs the current password, so
    someone using a computer left logged in can't lock the owner out."""
    user = current_user._get_current_object()
    home = url_for(f"{user.get_id().split(':')[0]}.dashboard")
    if request.method == "POST":
        if not user.check_password(request.form.get("current_password", "")):
            flash("Your current password isn't right. Please try again.", "error")
        else:
            error = password_error(request.form.get("password", ""), request.form.get("confirm_password", ""))
            if error:
                flash(error, "error")
            else:
                user.set_password(request.form["password"])   # any unused reset links stop working too
                db.session.commit()
                flash("Your password has been changed.", "success")
                return redirect(home)
    return render_template("auth/change_password.html", home=home)


@bp.route("/logout")
@login_required
def logout():
    was_admin = isinstance(current_user._get_current_object(), Admin)
    logout_user()
    return redirect(url_for("admin.login" if was_admin else "auth.login"))
