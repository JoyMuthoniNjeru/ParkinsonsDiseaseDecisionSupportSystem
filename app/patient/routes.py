from datetime import datetime, date
from decimal import Decimal, InvalidOperation
from flask import render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from app.patient import bp
from app.extensions import db
from app.validators import password_error
from app.models import (
    Patient, SymptomType, MedicationReference, PatientMedication, MedicationSchedule,
    SymptomRecord, MedicationLog, CaregiverLink, Caregiver, Alert, Insight,
    MEDICATION_FORMS, DOSE_UNITS,
)
from app.rule_engine import evaluate_symptom_entry, evaluate_medication_log


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


@bp.route("/medications")
@login_required
def medications():
    guard = _require_patient()
    if guard:
        return guard

    my_meds = PatientMedication.query.filter_by(patient_id=current_user.patient_id).all()
    return render_template("patient/medications.html", my_meds=my_meds)


# Times-per-day chips pre-fill the dose time inputs with these (patient can edit them)
DOSE_TIME_PRESETS = {
    1: ["08:00"],
    2: ["08:00", "20:00"],
    3: ["08:00", "13:00", "18:00"],
    4: ["08:00", "12:00", "16:00", "20:00"],
    5: ["07:00", "10:00", "13:00", "16:00", "19:00"],
}


def _parse_medication_form(form):
    """Validate the Add medication form. Returns (error, None) or (None, data)."""
    med = db.session.get(MedicationReference, form.get("medication_id", type=int) or 0)
    if not med:
        return "Please choose a medication from the list.", None
    if form.get("form") not in MEDICATION_FORMS:
        return "Please choose a form (tablet, capsule, patch or liquid).", None
    if form.get("dose_unit") not in DOSE_UNITS:
        return "Please choose a unit (mg, mcg or mg/24h).", None

    try:
        dose_amount = Decimal(form.get("dose_amount", ""))
        quantity = Decimal(form.get("quantity_per_dose", "1"))
    except InvalidOperation:
        return "Dose and amount per dose must be numbers.", None
    if not dose_amount.is_finite() or dose_amount <= 0:
        return "Please enter the dose, e.g. 100.", None
    if not quantity.is_finite() or quantity <= 0 or quantity > 10 or quantity % Decimal("0.5"):
        return "Amount per dose must be in steps of 0.5, e.g. 1 or 1.5.", None

    n = form.get("times_per_day", type=int)
    if n not in DOSE_TIME_PRESETS:
        return "Please choose how many times a day.", None
    try:
        # Only the first n time inputs are shown; the rest are hidden
        times = sorted({datetime.strptime(t, "%H:%M").time() for t in form.getlist("dose_times")[:n]})
    except ValueError:
        return "Please fill in every dose time.", None
    if len(times) != n:
        return "Please give each dose a different time.", None

    try:
        start = date.fromisoformat(form["started_date"]) if form.get("started_date") else date.today()
        end = date.fromisoformat(form["end_date"]) if form.get("end_date") else None
    except ValueError:
        return "Please enter valid dates.", None
    if end and end < start:
        return "The end date can't be before the start date.", None

    return None, {
        "med": {
            "medication_id": med.medication_id,
            "dose_amount": dose_amount,
            "dose_unit": form["dose_unit"],
            "strength": form.get("strength", "").strip() or None,
            "form": form["form"],
            "quantity_per_dose": quantity,
            "started_date": start,
            "end_date": end,
            "instructions": form.get("instructions", "").strip() or None,
        },
        "times": times,
    }


@bp.route("/medications/add", methods=["GET", "POST"])
@login_required
def add_medication():
    guard = _require_patient()
    if guard:
        return guard

    if request.method == "POST":
        error, data = _parse_medication_form(request.form)
        if error:
            flash(error, "error")
        else:
            pm = PatientMedication(patient_id=current_user.patient_id, **data["med"])
            pm.schedules = [MedicationSchedule(dose_time=t) for t in data["times"]]
            db.session.add(pm)
            db.session.commit()
            flash(f"Added {pm.dose_label()}, {len(data['times'])}x a day.", "success")
            return redirect(url_for("patient.medications"))

    catalog = MedicationReference.query.order_by(MedicationReference.name).all()
    # request.form is empty on GET, and holds the patient's answers after an error
    return render_template("patient/add_medication.html", catalog=catalog, values=request.form,
                           forms=MEDICATION_FORMS, units=DOSE_UNITS, presets=DOSE_TIME_PRESETS,
                           today=date.today().isoformat())


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
                status=request.form["adherence_status"],
                taken_at=datetime.utcnow() if request.form["adherence_status"] == "taken" else None,
            )
            db.session.add(log)
            db.session.commit()
            evaluate_medication_log(log)
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
