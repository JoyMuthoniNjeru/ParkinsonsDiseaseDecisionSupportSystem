from datetime import datetime, date, timedelta
from decimal import Decimal, InvalidOperation
from flask import render_template, request, redirect, url_for, flash, session
from flask_login import login_required, current_user
from app.patient import bp
from app.extensions import db
from app.validators import password_error
from app.models import (
    Patient, SymptomType, MedicationReference, PatientMedication, MedicationSchedule,
    SymptomRecord, MedicationLog, CaregiverLink, Caregiver, Alert,
    MEDICATION_FORMS, DOSE_UNITS, SEVERITY_LABELS, SEVERITY_MAX,
)
from app.rule_engine import evaluate_symptom_entry, evaluate_medication_log
from app.dashboard_data import todays_doses, patient_overview, LATE_AFTER


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
        if request.form.get("diagnosis_year"):  # optional
            year = request.form.get("diagnosis_year", type=int)
            error = patient.diagnosis_year_error(year)
            if error:
                flash(error, "error")
                return redirect(url_for("patient.register"))
            patient.diagnosis_year = year
        patient.set_password(request.form["password"])
        db.session.add(patient)
        db.session.commit()
        flash("Account created. You can now log in.", "success")
        return redirect(url_for("auth.login"))

    return render_template("patient/register.html", this_year=date.today().year)


@bp.route("/diagnosis-year", methods=["POST"])
@login_required
def set_diagnosis_year():
    """Add or change the year of diagnosis (form in the dashboard's Insights card)."""
    guard = _require_patient()
    if guard:
        return guard
    year = request.form.get("diagnosis_year", type=int)
    error = current_user.diagnosis_year_error(year)
    if error:
        flash(error, "warning")
    else:
        current_user.diagnosis_year = year
        db.session.commit()
        flash(f"Year of diagnosis saved ({year}). New insights will compare your scores with "
              f"people at the same stage.", "success")
    return redirect(url_for("patient.dashboard"))


@bp.route("/dashboard")
@login_required
def dashboard():
    guard = _require_patient()
    if guard:
        return guard

    overview = patient_overview(current_user.patient_id)
    my_meds = PatientMedication.query.filter_by(patient_id=current_user.patient_id).all()

    # Show the "Undo" bar once, right after a dose was logged
    undo = session.get("undo_dose")
    if undo and not undo.get("shown"):
        session["undo_dose"] = {**undo, "shown": True}
    else:
        undo = None

    return render_template("patient/dashboard.html", **overview, my_meds=my_meds, undo=undo,
                           symptom_types=SymptomType.loggable(), severity_labels=SEVERITY_LABELS,
                           today_year=date.today().year)


# ---------------------------------------------------------------------------
# Today's doses: built from the schedules (app/dashboard_data.py), logged with one tap
# ---------------------------------------------------------------------------

UNDO_WINDOW = timedelta(minutes=10)  # how long the patient can undo a dose they just logged


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
    row = next((r for r in todays_doses(current_user.patient_id)
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
    # Picking a medication pre-fills its usual strength, unit, form and times per day (from PPMI)
    prefill = {m.medication_id: m.prefill() for m in catalog}
    # request.form is empty on GET, and holds the patient's answers after an error
    return render_template("patient/add_medication.html", catalog=catalog, values=request.form,
                           forms=MEDICATION_FORMS, units=DOSE_UNITS, presets=DOSE_TIME_PRESETS,
                           prefill=prefill, today=date.today().isoformat())


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
        # The quick-log card on the dashboard posts here too; send the patient back to it
        back = url_for("patient.dashboard" if request.form.get("next") == "dashboard" else "patient.report")
        error, records = _parse_symptom_form(request.form)
        if error:
            flash(error, "warning")
            return redirect(back)

        db.session.add_all(records)
        db.session.commit()
        alerts = sum(1 for r in records if evaluate_symptom_entry(r))
        message = f"Saved {len(records)} symptom{'s' if len(records) != 1 else ''}."
        if alerts:
            message += (f" {alerts} scored Moderate or worse, so your caregiver and clinician "
                        f"will see {'an alert' if alerts == 1 else 'alerts'}.")
        flash(message, "success")
        return redirect(back)

    loggable = SymptomType.loggable()
    groups = [("Non-motor", "How you feel and sleep", [s for s in loggable if s.category == "non-motor"]),
              ("Motor", "Movement and daily activities", [s for s in loggable if s.category == "motor"])]
    return render_template("patient/report.html", groups=groups, severity_labels=SEVERITY_LABELS)


def _parse_symptom_form(form):
    """Symptom entries from either form. Returns (error, None) or (None, [SymptomRecord, ...]).
    - Log symptom page: one field per item, severity_<symptom_type_id> (+ optional note_<id>);
      items left blank are skipped.
    - Dashboard quick-log: a single symptom_type_id + severity.
    Everything is checked before anything is saved."""
    loggable = {s.symptom_type_id: s for s in SymptomType.loggable()}
    if "symptom_type_id" in form:
        chosen = {form.get("symptom_type_id", type=int): (form.get("severity"), form.get("notes"))}
    else:
        chosen = {sid: (form.get(f"severity_{sid}"), form.get(f"note_{sid}")) for sid in loggable
                  if form.get(f"severity_{sid}") not in (None, "")}
    if not chosen or all(raw in (None, "") for raw, _ in chosen.values()):
        return f"Choose a score from 0 ({SEVERITY_LABELS[0]}) to {SEVERITY_MAX} " \
               f"({SEVERITY_LABELS[-1]}) for at least one symptom.", None

    records = []
    for sid, (raw, note) in chosen.items():
        if sid not in loggable:
            return "Please choose a symptom from the list.", None
        try:
            severity = int(raw)
        except (TypeError, ValueError):
            severity = -1
        if not 0 <= severity <= SEVERITY_MAX:
            return f"{loggable[sid].name}: choose a score from 0 to {SEVERITY_MAX}.", None
        records.append(SymptomRecord(patient_id=current_user.patient_id, symptom_type_id=sid,
                                     severity=severity, notes=(note or "").strip() or None))
    return None, records


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
                flash("A request to this caregiver is already pending.", "warning")
            else:  # approved
                flash("This caregiver is already linked to you.", "warning")
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
    name = link.caregiver.name
    db.session.delete(link)
    db.session.commit()
    flash(f"{name} can no longer see your dashboard.", "success")
    return redirect(url_for("patient.caregivers"))
