from flask import render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from app.admin import bp
from app.extensions import db
from app.models import Admin, Patient, Caregiver, HealthcareProfessional


def _require_admin():
    if not isinstance(current_user, Admin):
        flash("Please log in as an admin.")
        return redirect(url_for("auth.login"))
    return None


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
    patient = Patient.query.get_or_404(patient_id)
    patient.account_status = "deactivated" if patient.account_status == "active" else "active"
    db.session.commit()
    return redirect(url_for("admin.dashboard"))


@bp.route("/caregiver/<int:caregiver_id>/toggle", methods=["POST"])
@login_required
def toggle_caregiver(caregiver_id):
    guard = _require_admin()
    if guard:
        return guard
    caregiver = Caregiver.query.get_or_404(caregiver_id)
    caregiver.account_status = "deactivated" if caregiver.account_status == "active" else "active"
    db.session.commit()
    return redirect(url_for("admin.dashboard"))


@bp.route("/hcp/<int:hcp_id>/decide", methods=["POST"])
@login_required
def decide_hcp(hcp_id):
    guard = _require_admin()
    if guard:
        return guard
    hcp = HealthcareProfessional.query.get_or_404(hcp_id)
    hcp.credential_status = "approved" if request.form["decision"] == "approve" else "rejected"
    db.session.commit()
    return redirect(url_for("admin.dashboard"))
