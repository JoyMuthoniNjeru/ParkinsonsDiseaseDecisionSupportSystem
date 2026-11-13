from flask import render_template, request, redirect, url_for, flash
from flask_login import login_required, login_user, current_user
from app.admin import bp
from app.extensions import db
from sqlalchemy import func
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
    # For each pending application: the patient emails it listed, and who (if anyone) they belong to
    requested = {h.hcp_id: requested_patients(h) for h in hcps if h.credential_status == "pending"}
    return render_template("admin/dashboard.html", patients=patients, caregivers=caregivers, hcps=hcps,
                           requested=requested)


def requested_patients(hcp):
    """[(email, Patient or None), ...] for the emails an HCP listed at sign-up (case-insensitive)."""
    emails = hcp.requested_emails()
    found = {p.email.lower(): p for p in
             Patient.query.filter(func.lower(Patient.email).in_(emails)).all()} if emails else {}
    return [(email, found.get(email)) for email in emails]


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
    if request.form["decision"] != "approve":
        hcp.credential_status = "rejected"            # a rejected application assigns nobody
        db.session.commit()
        flash(f"{hcp.name}'s application was rejected.", "success")
        return redirect(url_for("admin.dashboard"))

    # Approve + assign the patients they listed (80% check): patients without a clinician are
    # assigned; patients who already have one only if the Admin ticked "Reassign" for them.
    hcp.credential_status = "approved"
    reassign = set(request.form.getlist("reassign", type=int))
    assigned = [p for _, p in requested_patients(hcp)
                if p is not None and (p.hcp_id is None or p.patient_id in reassign)]
    for p in assigned:
        p.hcp_id = hcp.hcp_id
    db.session.commit()
    names = ", ".join(p.name for p in assigned)
    flash(f"{hcp.name}'s application was approved."
          + (f" Assigned to them: {names}." if assigned else ""), "success")
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