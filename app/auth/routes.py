from flask import render_template, request, redirect, url_for, flash
from flask_login import login_user, logout_user, login_required
from app.auth import bp
from app.models import Patient, Caregiver, HealthcareProfessional, Admin

ROLE_MODELS = {
    "patient": Patient,
    "caregiver": Caregiver,
    "hcp": HealthcareProfessional,
    "admin": Admin,
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


@bp.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))
