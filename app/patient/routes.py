from datetime import datetime, date, time, timedelta
from decimal import Decimal, InvalidOperation
from flask import render_template, request, redirect, url_for, flash, session, current_app
from flask_login import login_required, current_user
from sqlalchemy import or_
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
    doses = _todays_doses(current_user.patient_id)
    doses_done = sum(1 for d in doses if d["state"] in ("taken", "late"))
    my_meds = PatientMedication.query.filter_by(patient_id=current_user.patient_id).all()

    # Show the "Undo" bar once, right after a dose was logged
    undo = session.get("undo_dose")
    if undo and not undo.get("shown"):
        session["undo_dose"] = {**undo, "shown": True}
    else:
        undo = None

    return render_template("patient/dashboard.html",
                            symptom_records=symptom_records, alerts=alerts, insights=insights,
                            doses=doses, doses_done=doses_done, my_meds=my_meds, undo=undo)


# ---------------------------------------------------------------------------
# Today's doses: built from the schedules, logged with one tap
# ---------------------------------------------------------------------------

LATE_AFTER = timedelta(minutes=30)   # taken more than 30 min after the scheduled time = "late"
DUE_WINDOW = timedelta(minutes=30)   # a dose shows "Due now" from 30 min before its time
UNDO_WINDOW = timedelta(minutes=10)  # how long the patient can undo a dose they just logged


def _todays_doses(patient_id):
    """One row per scheduled dose today, oldest first, with its log (if any) and a state:
    taken / late / missed / skipped (logged) or due / upcoming (not logged yet)."""
    offset = timedelta(hours=current_app.config["DISPLAY_UTC_OFFSET_HOURS"])
    now = datetime.utcnow()
    today = (now + offset).date()
    day_start = datetime.combine(today, time.min) - offset  # local midnight, in UTC

    meds = PatientMedication.query.filter(
        PatientMedication.patient_id == patient_id,
        PatientMedication.started_date <= today,
        or_(PatientMedication.end_date.is_(None), PatientMedication.end_date >= today),
    ).all()
    schedules = [s for pm in meds for s in pm.schedules]

    logs = {}
    if schedules:
        todays_logs = MedicationLog.query.filter(
            MedicationLog.schedule_id.in_([s.schedule_id for s in schedules]),
            MedicationLog.scheduled_time >= day_start,
            MedicationLog.scheduled_time < day_start + timedelta(days=1),
        )
        logs = {log.schedule_id: log for log in todays_logs}

    rows = []
    for s in schedules:
        scheduled = datetime.combine(today, s.dose_time) - offset  # stored in UTC like the logs
        log = logs.get(s.schedule_id)
        if log:
            state = log.status
        elif now >= scheduled - DUE_WINDOW:
            state = "due"
        else:
            state = "upcoming"
        rows.append({"schedule": s, "pm": s.patient_medication, "scheduled": scheduled,
                     "log": log, "state": state})
    rows.sort(key=lambda r: r["scheduled"])
    return rows


def _remember_for_undo(log, alerts, message):
    session["undo_dose"] = {"log_id": log.log_id, "alert_ids": [a.alert_id for a in alerts],
                            "message": message}


@bp.route("/doses/log", methods=["POST"])
@login_required
def log_dose():
    guard = _require_patient()
    if guard:
        return guard

    schedule_id = request.form.get("schedule_id", type=int)
    action = request.form.get("status")
    row = next((r for r in _todays_doses(current_user.patient_id)
                if r["schedule"].schedule_id == schedule_id), None)
    if not row or action not in ("taken", "missed"):
        flash("Couldn't log that dose. Please try again.", "error")
        return redirect(url_for("patient.dashboard"))
    if row["log"]:
        flash("That dose is already logged.", "error")
        return redirect(url_for("patient.dashboard"))
    if row["state"] == "upcoming":
        flash("That dose isn't due yet.", "error")
        return redirect(url_for("patient.dashboard"))

    pm = row["pm"]
    now = datetime.utcnow()
    log = MedicationLog(patient_medication_id=pm.patient_medication_id, schedule_id=schedule_id,
                        scheduled_time=row["scheduled"])
    if action == "taken":
        log.status = "late" if now > row["scheduled"] + LATE_AFTER else "taken"
        log.taken_at = now
        log.quantity_taken = pm.quantity_per_dose
    else:
        log.status = "missed"
    db.session.add(log)
    db.session.commit()

    alerts = evaluate_medication_log(log)
    dose_time = row["schedule"].dose_time.strftime("%H:%M")
    _remember_for_undo(log, alerts,
                       f"{pm.dose_label()} at {dose_time} marked as {log.status}.")
    return redirect(url_for("patient.dashboard"))


@bp.route("/doses/extra", methods=["POST"])
@login_required
def log_extra_dose():
    """A dose taken outside the schedule (schedule_id stays null)."""
    guard = _require_patient()
    if guard:
        return guard

    pm = db.session.get(PatientMedication, request.form.get("patient_medication_id", type=int) or 0)
    if not pm or pm.patient_id != current_user.patient_id:
        flash("Please choose one of your medications.", "error")
        return redirect(url_for("patient.dashboard"))

    log = MedicationLog(patient_medication_id=pm.patient_medication_id, status="taken",
                        taken_at=datetime.utcnow(), quantity_taken=pm.quantity_per_dose,
                        note="Extra dose")
    db.session.add(log)
    db.session.commit()
    _remember_for_undo(log, [], f"Extra dose of {pm.dose_label()} logged.")
    return redirect(url_for("patient.dashboard"))


@bp.route("/doses/undo", methods=["POST"])
@login_required
def undo_dose():
    guard = _require_patient()
    if guard:
        return guard

    undo = session.pop("undo_dose", None)
    log = db.session.get(MedicationLog, undo["log_id"]) if undo else None
    if (not log or log.patient_medication.patient_id != current_user.patient_id
            or datetime.utcnow() - log.timestamp > UNDO_WINDOW):
        flash("That dose can no longer be undone.", "error")
        return redirect(url_for("patient.dashboard"))

    # Also remove any alert the Rule Engine raised for this dose
    Alert.query.filter(Alert.alert_id.in_(undo["alert_ids"]),
                       Alert.patient_id == current_user.patient_id).delete(synchronize_session=False)
    db.session.delete(log)
    db.session.commit()
    flash("Undone. You can log that dose again.", "success")
    return redirect(url_for("patient.dashboard"))


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

        return redirect(url_for("patient.report"))

    symptom_types = SymptomType.query.all()
    return render_template("patient/report.html", symptom_types=symptom_types)


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
