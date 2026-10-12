from flask import render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from app.hcp import bp
from app.extensions import db
from app.validators import password_error
from app.models import HealthcareProfessional, Patient, SymptomRecord, Alert, Insight


def _require_hcp():
    if not isinstance(current_user, HealthcareProfessional):
        flash("Please log in as a healthcare professional.", "error")
        return redirect(url_for("auth.login"))
    return None


@bp.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        existing = HealthcareProfessional.query.filter_by(email=request.form["email"]).first()
        if existing:
            flash("An account with that email already exists.", "error")
            return redirect(url_for("hcp.register"))

        error = password_error(request.form["password"], request.form["confirm_password"])
        if error:
            flash(error, "error")
            return redirect(url_for("hcp.register"))

        hcp = HealthcareProfessional(
            name=request.form["name"],
            email=request.form["email"],
            institution=request.form["institution"],
            credential_status="pending",
        )
        hcp.set_password(request.form["password"])
        db.session.add(hcp)
        db.session.commit()
        flash("Application submitted. An Admin must approve your account before you can log in.", "success")
        return redirect(url_for("auth.login"))

    return render_template("hcp/register.html")


@bp.route("/dashboard")
@login_required
def dashboard():
    guard = _require_hcp()
    if guard:
        return guard

    patients = Patient.query.filter_by(hcp_id=current_user.hcp_id).all()
    return render_template("hcp/dashboard.html", patients=patients)


@bp.route("/patient/<int:patient_id>")
@login_required
def patient_dashboard(patient_id):
    guard = _require_hcp()
    if guard:
        return guard

    patient = Patient.query.filter_by(patient_id=patient_id, hcp_id=current_user.hcp_id).first_or_404()
    symptom_records = (
        SymptomRecord.query.filter_by(patient_id=patient_id)
        .order_by(SymptomRecord.timestamp.desc()).limit(10).all()
    )
    alerts = Alert.query.filter_by(patient_id=patient_id).order_by(Alert.timestamp.desc()).limit(10).all()
    insights = Insight.query.filter_by(patient_id=patient_id).order_by(Insight.generated_at.desc()).limit(5).all()
    return render_template("hcp/patient_dashboard.html", patient=patient,
                            symptom_records=symptom_records, alerts=alerts, insights=insights)
