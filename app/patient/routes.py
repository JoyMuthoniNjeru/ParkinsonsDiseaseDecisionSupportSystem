from datetime import datetime, date, timedelta
from flask import (render_template, request, redirect, url_for, flash, session, abort, current_app,
                   Response)
from flask_login import login_required, current_user
from app.patient import bp
from app.extensions import db
from app.validators import password_error
from app.models import (
    Patient, PatientMedication, SymptomRecord, MedicationLog, CaregiverLink, Caregiver, Alert,
    SEVERITY_LABELS, SEVERITY_MAX,
)
from app.rule_engine import (
    on_dose_corrected, on_symptom_edited, on_symptom_deleted,
)
from app import entries
from app import medications as prescriptions   # (the view below is also called "medications")
from app.links import expire_old_requests
from app.calendar_file import dose_calendar
from app.dashboard_data import (
    todays_doses, patient_overview, recent_dose_logs, can_correct_dose, can_edit_symptom,
    dose_day, LATE_AFTER, CORRECTION_DAYS,
)


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

        try:
            dob = datetime.strptime(request.form.get("date_of_birth", ""), "%Y-%m-%d").date()
        except ValueError:
            dob = None
        if dob is None or not date(1900, 1, 1) <= dob <= date.today():
            flash("Please enter a valid date of birth (it can't be in the future).", "error")
            return redirect(url_for("patient.register"))

        patient = Patient(
            name=request.form["name"],
            email=request.form["email"],
            date_of_birth=dob,
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

    return render_template("patient/register.html", this_year=date.today().year,
                           today=date.today().isoformat())


@bp.route("/diagnosis-year", methods=["POST"])
@login_required
def set_diagnosis_year():
    """Add or change the year of diagnosis (small card on My trends). Patients don't see insights
    (80% check), but the ML uses the year to compare with PPMI for the caregiver / HCP insights."""
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
        flash(f"Year of diagnosis saved ({year}). Your clinician can now compare your scores with "
              f"people at the same stage.", "success")
    return redirect(_back("patient.trends"))


def insights_message(count):
    """Flash text after "Update insights" on the HCP page."""
    if count == 0:
        return "Insights updated. There isn't enough logged yet to find patterns; keep logging daily."
    return f"Insights updated: {count} insight{'s' if count != 1 else ''} from the latest logs."


def _refresh_insights():
    """Rebuild this patient's insights after their data changed (see entries.refresh_insights)."""
    entries.refresh_insights(current_user)


def _undo_once():
    return entries.undo_once()


# ---------------------------------------------------------------------------
# Home = big tiles (one task per page, large targets for patients with tremor):
#   Today's doses -> /today, How are you feeling? -> /report, My trends -> /trends, Alerts -> /alerts.
# A dose that is due now can still be logged straight from home with one tap.
# ---------------------------------------------------------------------------

@bp.route("/dashboard")
@login_required
def dashboard():
    guard = _require_patient()
    if guard:
        return guard

    overview = patient_overview(current_user.patient_id)
    overview.pop("insights", None)          # insights are for the caregiver / HCP, not the patient
    # Greeting + date in local time: "Good afternoon, Amina · Wednesday 11 November"
    local_now = datetime.utcnow() + timedelta(hours=current_app.config["DISPLAY_UTC_OFFSET_HOURS"])
    greeting = ("Good morning" if local_now.hour < 12 else
                "Good afternoon" if local_now.hour < 17 else "Good evening")
    return render_template("patient/dashboard.html", **overview, undo=_undo_once(),
                           local_now=local_now, greeting=greeting)


@bp.route("/today")
@login_required
def today():
    """Today's doses: Taken / Missed, Change, Undo and extra doses."""
    guard = _require_patient()
    if guard:
        return guard
    doses = todays_doses(current_user.patient_id)
    my_meds = [pm for pm in PatientMedication.query.filter_by(patient_id=current_user.patient_id)
               if prescriptions.is_current(pm)]           # extra doses only of current medications
    done = sum(1 for d in doses if d["state"] in ("taken", "late"))
    return render_template("patient/today.html", doses=doses, my_meds=my_meds, done=done,
                           undo=_undo_once())


@bp.route("/trends")
@login_required
def trends():
    """My trends: trend chart, recent symptom entries (with Edit), year of diagnosis.
    No insights here: those are for the caregiver and HCP (80% check)."""
    guard = _require_patient()
    if guard:
        return guard
    overview = patient_overview(current_user.patient_id)
    overview.pop("insights", None)          # never handed to a patient page
    return render_template("patient/trends.html", **overview, today_year=date.today().year)


@bp.route("/alerts")
@login_required
def alerts():
    """Every alert the Rule Engine raised for this patient, newest first."""
    guard = _require_patient()
    if guard:
        return guard
    all_alerts = (Alert.query.filter_by(patient_id=current_user.patient_id)
                  .order_by(Alert.timestamp.desc()).limit(100).all())
    return render_template("patient/alerts.html", alerts=all_alerts)


# ---------------------------------------------------------------------------
# Today's doses: built from the schedules (app/dashboard_data.py), logged with one tap
# ---------------------------------------------------------------------------

def _remember_for_undo(log, message):
    entries.remember_for_undo(log, message)


@bp.route("/doses/log", methods=["POST"])
@login_required
def log_dose():
    guard = _require_patient()
    if guard:
        return guard
    log, error = entries.log_scheduled_dose(current_user, request.form.get("schedule_id", type=int),
                                            request.form.get("status"))
    if error:
        flash(error, "error")
        return redirect(_back("patient.today"))
    _remember_for_undo(log, f"{log.patient_medication.dose_label()} at "
                            f"{log.schedule.dose_time:%H:%M} marked as {log.status}.")
    return redirect(_back("patient.today"))


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
        return redirect(_back("patient.today"))

    log = MedicationLog(patient_medication_id=pm.patient_medication_id, status="taken",
                        taken_at=datetime.utcnow(), quantity_taken=pm.quantity_per_dose,
                        note="Extra dose")
    db.session.add(log)
    db.session.commit()
    _refresh_insights()
    _remember_for_undo(log, f"Extra dose of {pm.dose_label()} logged.")
    return redirect(_back("patient.today"))


@bp.route("/doses/undo", methods=["POST"])
@login_required
def undo_dose():
    guard = _require_patient()
    if guard:
        return guard

    log = entries.take_undo_log()
    # Only the patient's own dose, logged by the patient (a caregiver undoes their own logs)
    if (not entries.can_still_undo(log) or log.patient_medication.patient_id != current_user.patient_id
            or log.logged_by_caregiver_id is not None):
        flash("That dose can no longer be undone.", "error")
        return redirect(_back("patient.today"))

    entries.delete_dose_log(log, current_user)
    flash("Undone. You can log that dose again.", "success")
    return redirect(_back("patient.today"))


# ---------------------------------------------------------------------------
# Corrections: change a logged dose (today + 2 days before), edit a symptom entry (same day).
# The window is checked here on the server; the rule engine then re-checks the alerts.
# ---------------------------------------------------------------------------

DOSE_REASONS = ["Forgot", "Side effects", "Felt unwell", "Ran out", "Other"]


# Forms and links send ?next=<page> so the patient lands back on the page they acted from
BACK_PAGES = {"dashboard": "patient.dashboard", "today": "patient.today", "trends": "patient.trends",
              "medications": "patient.medications", "report": "patient.report"}


def _back(default="patient.dashboard"):
    """URL of the page an action was started from (only known pages; anything else -> default)."""
    return url_for(BACK_PAGES.get(request.values.get("next"), default))


def _own_log(log_id):
    """The patient's own dose log, or 404 (also for another patient's log)."""
    log = db.session.get(MedicationLog, log_id)
    if not log or log.patient_medication.patient_id != current_user.patient_id:
        abort(404)
    return log


def _own_record(record_id):
    record = db.session.get(SymptomRecord, record_id)
    if not record or record.patient_id != current_user.patient_id:
        abort(404)
    return record


def _alert_note(removed, created):
    parts = []
    if removed:
        parts.append(f"{removed} alert{'s' if removed != 1 else ''} removed")
    if created:
        parts.append(f"{len(created)} new alert{'s' if len(created) != 1 else ''} for your caregiver and clinician")
    return f" ({', '.join(parts)})" if parts else ""


@bp.route("/doses/<int:log_id>/change", methods=["GET", "POST"])
@login_required
def change_dose(log_id):
    guard = _require_patient()
    if guard:
        return guard
    log = _own_log(log_id)
    if not can_correct_dose(log):
        flash(f"Only doses from today and the {CORRECTION_DAYS - 1} days before can be changed.", "warning")
        return redirect(_back())
    if log.schedule_id is None:
        flash("Extra doses can't be changed, but you can delete one logged by mistake.", "warning")
        return redirect(_back())

    pm = log.patient_medication
    offset = timedelta(hours=current_app.config["DISPLAY_UTC_OFFSET_HOURS"])
    if request.method == "POST":
        choice = request.form.get("status")
        if choice not in ("taken", "missed", "skipped"):
            flash("Please choose Taken, Missed or Skipped.", "warning")
            return redirect(request.url)
        taken_at = None
        if choice == "taken":
            try:
                clock = datetime.strptime(request.form.get("taken_time", ""), "%H:%M").time()
            except ValueError:
                flash("Please enter the time you took it.", "warning")
                return redirect(request.url)
            taken_at = datetime.combine(dose_day(log), clock) - offset  # local -> UTC
            if taken_at > datetime.utcnow():
                flash("The time taken can't be in the future.", "warning")
                return redirect(request.url)
        reason = request.form.get("reason") if request.form.get("reason") in DOSE_REASONS else None

        old_status = log.status
        if log.original_status is None:          # trace: keep the status from before the FIRST correction
            log.original_status = old_status
        log.status = ("late" if taken_at > log.scheduled_time + LATE_AFTER else "taken") if taken_at else choice
        log.taken_at = taken_at
        log.quantity_taken = pm.quantity_per_dose if taken_at else None
        log.reason = reason
        log.note = request.form.get("note", "").strip() or None
        log.corrected_at = datetime.utcnow()
        db.session.commit()
        if session.get("undo_dose", {}).get("log_id") == log.log_id:
            session.pop("undo_dose")             # Undo no longer applies to a corrected dose

        removed, created = on_dose_corrected(log, old_status)
        _refresh_insights()
        when = f" ({log.taken_at + offset:%H:%M})" if log.taken_at else ""
        flash(f"Updated: {pm.dose_label()} at {log.schedule.dose_time:%H:%M} on {dose_day(log):%d %b} "
              f"is now {log.status}{when}{_alert_note(removed, created)}.", "success")
        return redirect(_back())

    default_time = (log.taken_at + offset) if log.taken_at else (log.scheduled_time + offset)
    return render_template("patient/change_dose.html", log=log, pm=pm, reasons=DOSE_REASONS,
                           choice="taken" if log.status in ("taken", "late") else log.status,
                           default_time=default_time.strftime("%H:%M"), day=dose_day(log),
                           next=request.values.get("next", "dashboard"))


@bp.route("/doses/<int:log_id>/delete", methods=["POST"])
@login_required
def delete_extra_dose(log_id):
    """Delete an extra (unscheduled) dose logged by mistake, within the correction window."""
    guard = _require_patient()
    if guard:
        return guard
    log = _own_log(log_id)
    if log.schedule_id is not None:
        flash("Scheduled doses can't be deleted. Use Change instead.", "warning")
    elif not can_correct_dose(log):
        flash(f"Only doses from today and the {CORRECTION_DAYS - 1} days before can be deleted.", "warning")
    else:
        label = log.patient_medication.dose_label()
        Alert.query.filter(Alert.medication_log_id == log.log_id).delete(synchronize_session=False)
        db.session.delete(log)
        db.session.commit()
        _refresh_insights()
        flash(f"Deleted the extra dose of {label}.", "success")
    return redirect(_back("patient.medications"))


@bp.route("/symptoms/<int:record_id>/edit", methods=["GET", "POST"])
@login_required
def edit_symptom(record_id):
    guard = _require_patient()
    if guard:
        return guard
    record = _own_record(record_id)
    if record.caregiver_author:
        flash(_logged_by_caregiver_message(record, "changed"), "warning")
        return redirect(url_for("patient.trends"))
    if not can_edit_symptom(record):
        flash("Symptom entries can only be changed on the day they were logged.", "warning")
        return redirect(url_for("patient.trends"))

    if request.method == "POST":
        severity = request.form.get("severity", type=int)
        if severity is None or not 0 <= severity <= SEVERITY_MAX:
            flash(f"Choose a score from 0 ({SEVERITY_LABELS[0]}) to {SEVERITY_MAX} ({SEVERITY_LABELS[-1]}).", "warning")
            return redirect(request.url)
        if record.original_severity is None:     # trace: keep the score from before the FIRST edit
            record.original_severity = record.severity
        record.severity = severity
        record.notes = request.form.get("notes", "").strip() or None
        record.edited_at = datetime.utcnow()
        db.session.commit()
        alert = on_symptom_edited(record)
        _refresh_insights()
        extra = " It scored Moderate or worse, so your caregiver and clinician will see an alert." if alert else ""
        flash(f"Updated {record.symptom_type.name} to {record.severity_text()}.{extra}", "success")
        return redirect(url_for("patient.trends"))

    return render_template("patient/edit_symptom.html", record=record, severity_labels=SEVERITY_LABELS)


def _logged_by_caregiver_message(record, action):
    """Patients edit only their own entries; one their caregiver logged stays as the caregiver's."""
    return (f"This entry was logged by your caregiver ({record.caregiver_author.name}), "
            f"so it can't be {action} here.")


@bp.route("/symptoms/<int:record_id>/delete", methods=["POST"])
@login_required
def delete_symptom(record_id):
    guard = _require_patient()
    if guard:
        return guard
    record = _own_record(record_id)
    if record.caregiver_author:
        flash(_logged_by_caregiver_message(record, "deleted"), "warning")
    elif not can_edit_symptom(record):
        flash("Symptom entries can only be deleted on the day they were logged.", "warning")
    else:
        name = record.symptom_type.name
        on_symptom_deleted(record)               # remove its alert first
        db.session.delete(record)
        db.session.commit()
        _refresh_insights()
        flash(f"Deleted the {name} entry.", "success")
    return redirect(url_for("patient.trends"))


@bp.route("/medications")
@login_required
def medications():
    guard = _require_patient()
    if guard:
        return guard

    # Read-only for the patient (80% check): their clinician adds, changes and stops medications
    my_meds = PatientMedication.query.filter_by(patient_id=current_user.patient_id).all()
    current = [pm for pm in my_meds if prescriptions.is_current(pm)]
    stopped = [pm for pm in my_meds if not prescriptions.is_current(pm)]
    return render_template("patient/medications.html", my_meds=current, stopped=stopped,
                           recent_logs=recent_dose_logs(current_user.patient_id),
                           correction_days=CORRECTION_DAYS)


@bp.route("/medications/calendar.ics")
@login_required
def dose_calendar_file():
    """"Add dose reminders to my calendar": an .ics file with a daily repeating event + alert per
    dose time of each current medication (80% check: reminders without SMS; app/calendar_file.py)."""
    guard = _require_patient()
    if guard:
        return guard
    current = [pm for pm in PatientMedication.query.filter_by(patient_id=current_user.patient_id)
               if prescriptions.is_current(pm)]
    ics = dose_calendar(current, current_app.config["DISPLAY_UTC_OFFSET_HOURS"])
    return Response(ics, mimetype="text/calendar",
                    headers={"Content-Disposition": 'attachment; filename="pd-compass-doses.ics"'})


@bp.route("/report", methods=["GET", "POST"])
@login_required
def report():
    guard = _require_patient()
    if guard:
        return guard

    if request.method == "POST":
        # The quick-log card on the dashboard posts here too; send the patient back to it
        back = _back("patient.report")
        error, records = entries.parse_symptom_form(request.form, current_user.patient_id)
        if error:
            flash(error, "warning")
            return redirect(back)
        alerts = entries.save_symptoms(records, current_user)
        flash(entries.saved_message(len(records), alerts), "success")
        return redirect(back)

    return render_template("patient/report.html", groups=entries.symptom_groups(),
                           severity_labels=SEVERITY_LABELS)


@bp.route("/caregivers", methods=["GET", "POST"])
@login_required
def caregivers():
    guard = _require_patient()
    if guard:
        return guard

    expire_old_requests()          # requests unanswered for 24 h show as Expired (can be re-sent)
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
                flash("Link request sent. It expires if not answered within 24 hours.", "success")
            elif existing.status in ("declined", "expired"):
                existing.status = "pending"
                existing.linked_date = datetime.utcnow()     # a fresh 24 hours
                db.session.commit()
                flash("Link request re-sent. It expires if not answered within 24 hours.", "success")
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
    link = db.get_or_404(CaregiverLink, link_id)
    if link.patient_id != current_user.patient_id:
        flash("Not allowed.", "error")
        return redirect(url_for("patient.caregivers"))
    name = link.caregiver.name
    db.session.delete(link)
    db.session.commit()
    flash(f"{name} can no longer see your dashboard.", "success")
    return redirect(url_for("patient.caregivers"))
