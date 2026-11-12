from flask import render_template, request, redirect, url_for, flash
from flask_login import login_required, login_user, current_user
from app.admin import bp
from app.extensions import db
from app.models import Admin, Patient, Caregiver, HealthcareProfessional


def _require_admin():
    if not isinstance(current_user, Admin):
        flash("Please log in as an admin.", "error")
        return redirect(url_for("auth.login"))
    return None


@bp.route("/login", methods=["GET", "POST"])
def login():
    """The Admin's own log in (back-office user): not linked from the app and not on the public
    log in page. The protection is still the password + the Admin check on every admin page;
    this page just keeps the admin role out of the patients' / caregivers' / clinicians' way."""
    if request.method == "POST":
        admin = Admin.query.filter_by(email=request.form.get("email")).first()
        if admin is None or not admin.check_password(request.form.get("password", "")):
            flash("Incorrect email or password.", "error")
            return redirect(url_for("admin.login"))
        login_user(admin)
        return redirect(url_for("admin.dashboard"))
    return render_template("admin/login.html")


@bp.route("/dashboard")
@login_required
def dashboard():
    """
    Admin's one function: Manage user profiles -- covers plain
    activate/deactivate for Patient/Caregiver AND approve/reject for
    pending HCP applications (merged per the supervisor-approved scope
    reduction: HCP review is just a status change, same as any other
    account status change).
    """
    guard = _require_admin()
    if guard:
        return guard

    patients = Patient.query.all()
    caregivers = Caregiver.query.all()
    hcps = HealthcareProfessional.query.all()
    return render_template("admin/dashboard.html", patients=patients, caregivers=caregivers, hcps=hcps)


@bp.route("/patient/<int:patient_id>/toggle", methods=["POST"])
@login_required
def toggle_patient(patient_id):
    guard = _require_admin()
    if guard:
        return guard
    patient = db.get_or_404(Patient, patient_id)
    patient.account_status = "deactivated" if patient.account_status == "active" else "active"
    db.session.commit()
    flash(f"{patient.name}'s account is now {patient.account_status}.", "success")
    return redirect(url_for("admin.dashboard"))


@bp.route("/caregiver/<int:caregiver_id>/toggle", methods=["POST"])
@login_required
def toggle_caregiver(caregiver_id):
    guard = _require_admin()
    if guard:
        return guard
    caregiver = db.get_or_404(Caregiver, caregiver_id)
    caregiver.account_status = "deactivated" if caregiver.account_status == "active" else "active"
    db.session.commit()
    flash(f"{caregiver.name}'s account is now {caregiver.account_status}.", "success")
    return redirect(url_for("admin.dashboard"))


@bp.route("/hcp/<int:hcp_id>/decide", methods=["POST"])
@login_required
def decide_hcp(hcp_id):
    guard = _require_admin()
    if guard:
        return guard
    hcp = db.get_or_404(HealthcareProfessional, hcp_id)
    hcp.credential_status = "approved" if request.form["decision"] == "approve" else "rejected"
    db.session.commit()
    flash(f"{hcp.name}'s application was {hcp.credential_status}.", "success")
    return redirect(url_for("admin.dashboard"))

@bp.route("/patient/<int:patient_id>/assign", methods=["POST"])
@login_required
def assign_hcp(patient_id):
    guard = _require_admin()
    if guard:
        return guard

    patient = db.get_or_404(Patient, patient_id)
    hcp_id = request.form.get("hcp_id", "")

    if hcp_id == "":
        patient.hcp_id = None
        flash(f"{patient.name} is now unassigned.", "success")
    else:
        hcp = db.get_or_404(HealthcareProfessional, int(hcp_id))
        if hcp.credential_status != "approved":
            flash("Only approved healthcare professionals can be assigned.", "error")
            return redirect(url_for("admin.dashboard"))
        patient.hcp_id = hcp.hcp_id
        flash(f"{patient.name} assigned to {hcp.name}.", "success")

    db.session.commit()
    return redirect(url_for("admin.dashboard"))