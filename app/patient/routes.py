from datetime import datetime, date
from flask import render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from app.patient import bp
from app.extensions import db
from app.validators import password_error
from app.models import (
    Patient, SymptomType, MedicationReference, PatientMedication,
    SymptomRecord, MedicationLog, CaregiverLink, Caregiver, Alert, Insight,
)
from app.rule_engine import evaluate_symptom_entry, evaluate_medication_adherence


def _require_patient():
    """Guard: only a logged-in Patient may use these routes."""
    if not isinstance(current_user, Patient):
        flash("Please log in as a patient.", "error")
        return redirect(url_for("auth.login"))
    return None


@bp.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        existing = Patient.query.filter_by(email=request.form["email"]).first()
        if existing:
            flash("An account with that email already exists.", "error")
            return redirect(url_for("patient.register"))

        error = password_error(request.form["password"], request.form["confirm_password"])
        if error:
            flash(error, "error")
            return redirect(url_for("patient.register"))

        patient = Patient(
            name=request.form["name"],
            email=request.form["email"],
            date_of_birth=datetime.strptime(request.form["date_of_birth"], "%Y-%m-%d").date(),
        )
        patient.set_password(request.form["password"])
        db.session.add(patient)
        db.session.commit()
        flash("Account created. You can now log in.", "success")
        return redirect(url_for("auth.login"))

    return render_template("patient/register.html")


@bp.route("/dashboard")
@login_required
def dashboard():
    guard = _require_patient()
    if guard:
        return guard

    symptom_records = (
        SymptomRecord.query.filter_by(patient_id=current_user.patient_id)
        .order_by(SymptomRecord.timestamp.desc()).limit(10).all()
    )
    alerts = (
        Alert.query.filter_by(patient_id=current_user.patient_id)
        .order_by(Alert.timestamp.desc()).limit(10).all()
    )
    insights = (
        Insight.query.filter_by(patient_id=current_user.patient_id)
        .order_by(Insight.generated_at.desc()).limit(5).all()
    )
    return render_template("patient/dashboard.html",
                            symptom_records=symptom_records, alerts=alerts, insights=insights)


@bp.route("/medications", methods=["GET", "POST"])
@login_required
def medications():
    guard = _require_patient()
    if guard:
        return guard

    if request.method == "POST":
        med_id = int(request.form["medication_id"])
        already = PatientMedication.query.filter_by(
            patient_id=current_user.patient_id, medication_id=med_id
        ).first()
        if not already:
            pm = PatientMedication(patient_id=current_user.patient_id, medication_id=med_id,
                                    started_date=date.today())
            db.session.add(pm)
            db.session.commit()
        return redirect(url_for("patient.medications"))

    my_meds = PatientMedication.query.filter_by(patient_id=current_user.patient_id).all()
    catalog = MedicationReference.query.all()
    return render_template("patient/medications.html", my_meds=my_meds, catalog=catalog)


@bp.route("/medications/<int:pm_id>/remove", methods=["POST"])
@login_required
def remove_medication(pm_id):
    guard = _require_patient()
    if guard:
        return guard
    pm = PatientMedication.query.get_or_404(pm_id)
    if pm.patient_id != current_user.patient_id:
        flash("Not allowed.", "error")
        return redirect(url_for("patient.medications"))
    db.session.delete(pm)
    db.session.commit()
    return redirect(url_for("patient.medications"))


@bp.route("/report", methods=["GET", "POST"])
@login_required
def report():
    guard = _require_patient()
    if guard:
        return guard

    if request.method == "POST":
        entry_kind = request.form.get("entry_kind")

        if entry_kind == "symptom":
            record = SymptomRecord(
                patient_id=current_user.patient_id,
                symptom_type_id=int(request.form["symptom_type_id"]),
                severity=int(request.form["severity"]),
                notes=request.form.get("notes") or None,
            )
            db.session.add(record)
            db.session.commit()
            evaluate_symptom_entry(record)
            flash("Symptom entry logged.", "success")

        elif entry_kind == "medication":
            pm_id = int(request.form["patient_medication_id"])
            log = MedicationLog(
                patient_medication_id=pm_id,
                adherence_status=request.form["adherence_status"],
            )
            db.session.add(log)
            db.session.commit()
            pm = PatientMedication.query.get(pm_id)
            evaluate_medication_adherence(pm)
            flash("Medication entry logged.", "success")

        return redirect(url_for("patient.report"))

    symptom_types = SymptomType.query.all()
    my_meds = PatientMedication.query.filter_by(patient_id=current_user.patient_id).all()
    return render_template("patient/report.html", symptom_types=symptom_types, my_meds=my_meds)


@bp.route("/caregivers", methods=["GET", "POST"])
@login_required
def caregivers():
    guard = _require_patient()
    if guard:
        return guard

    if request.method == "POST":
        caregiver = Caregiver.query.filter_by(email=request.form["caregiver_email"]).first()
        if not caregiver:
            flash("No caregiver account found with that email.", "error")
        else:
            existing = CaregiverLink.query.filter_by(
                patient_id=current_user.patient_id, caregiver_id=caregiver.caregiver_id
            ).first()
            if not existing:
                link = CaregiverLink(patient_id=current_user.patient_id,
                                     caregiver_id=caregiver.caregiver_id, status="pending")
                db.session.add(link)
                db.session.commit()
                flash("Link request sent.", "success")
            elif existing.status == "declined":
                existing.status = "pending"
                db.session.commit()
                flash("Link request re-sent.", "success")
            elif existing.status == "pending":
                flash("A request to this caregiver is already pending.")
            else:  # approved
                flash("This caregiver is already linked to you.")
        return redirect(url_for("patient.caregivers"))

    links = CaregiverLink.query.filter_by(patient_id=current_user.patient_id).all()
    return render_template("patient/caregivers.html", links=links)


@bp.route("/caregivers/<int:link_id>/revoke", methods=["POST"])
@login_required
def revoke_caregiver(link_id):
    guard = _require_patient()
    if guard:
        return guard
    link = CaregiverLink.query.get_or_404(link_id)
    if link.patient_id != current_user.patient_id:
        flash("Not allowed.", "error")
        return redirect(url_for("patient.caregivers"))
    db.session.delete(link)
    db.session.commit()
    return redirect(url_for("patient.caregivers"))
