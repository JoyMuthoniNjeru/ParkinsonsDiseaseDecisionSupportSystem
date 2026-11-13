"""
Saving symptom entries and dose logs, shared by the patient's own pages and by a caregiver
logging for a linked patient (80% check, 12 Nov: "patients may be unable to log themselves").

Whoever logs it, the same things happen: the same checks, the same Rule Engine alerts and the
same automatic insight refresh. `caregiver_id` only records WHO logged it (None = the patient),
which is shown as a "Logged by <caregiver>" badge on every view.
"""
from datetime import datetime, timedelta

from flask import current_app, session

from app.extensions import db
from app.models import Alert, MedicationLog, SymptomRecord, SymptomType, SEVERITY_LABELS, SEVERITY_MAX
from app.rule_engine import evaluate_medication_log, evaluate_symptom_entry
from app.dashboard_data import todays_doses, LATE_AFTER
from app.ml_component import analyze_patient

UNDO_WINDOW = timedelta(minutes=10)   # how long a dose just logged can be undone (by whoever logged it)


# ---------------------------------------------------------------------------
# Insights
# ---------------------------------------------------------------------------

def refresh_insights(patient):
    """Rebuild a patient's insights right after their data changed (~0.1 s), so the caregiver and
    HCP always see current insights. Logging must never fail because of the analysis, so any
    error is written to the log and skipped (the entry itself is already saved)."""
    try:
        analyze_patient(patient)
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Automatic insight refresh failed")


# ---------------------------------------------------------------------------
# Symptoms
# ---------------------------------------------------------------------------

def symptom_groups():
    """The 26 loggable MDS-UPDRS items in the two groups shown on the Log symptoms page."""
    loggable = SymptomType.loggable()
    return [("Non-motor", "How you feel and sleep", [s for s in loggable if s.category == "non-motor"]),
            ("Motor", "Movement and daily activities", [s for s in loggable if s.category == "motor"])]


def parse_symptom_form(form, patient_id, caregiver_id=None):
    """Symptom entries from the Log symptoms form. Returns (error, None) or (None, [SymptomRecord]).
    One field per item: severity_<symptom_type_id> (+ optional note_<id>); blank items are skipped.
    (A single symptom_type_id + severity is still accepted.)
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
        records.append(SymptomRecord(patient_id=patient_id, symptom_type_id=sid, severity=severity,
                                     notes=(note or "").strip() or None,
                                     recorded_by_caregiver_id=caregiver_id))
    return None, records


def save_symptoms(records, patient):
    """Save parsed entries, run the Rule Engine on each, refresh insights.
    Returns how many entries raised an alert (score >= 3)."""
    db.session.add_all(records)
    db.session.commit()
    alerts = sum(1 for r in records if evaluate_symptom_entry(r))
    refresh_insights(patient)
    return alerts


def saved_message(count, alerts, whose="your"):
    """e.g. "Saved 2 symptoms. 1 scored Moderate or worse, so your caregiver and clinician ..." """
    message = f"Saved {count} symptom{'s' if count != 1 else ''}."
    if alerts:
        who = "your caregiver and clinician" if whose == "your" else f"{whose} clinician"
        message += (f" {alerts} scored Moderate or worse, so {who} will see "
                    f"{'an alert' if alerts == 1 else 'alerts'}.")
    return message


# ---------------------------------------------------------------------------
# Doses
# ---------------------------------------------------------------------------

def log_scheduled_dose(patient, schedule_id, action, caregiver_id=None):
    """Log one of today's scheduled doses as Taken (Late if > 30 min after its time) or Missed.
    Returns (log, None) or (None, error message). Raises alerts + refreshes insights."""
    row = next((r for r in todays_doses(patient.patient_id) if r["schedule"].schedule_id == schedule_id), None)
    if not row or action not in ("taken", "missed"):
        return None, "Couldn't log that dose. Please try again."
    if row["log"]:
        return None, "That dose is already logged."
    if row["state"] == "upcoming":
        return None, "That dose isn't due yet."

    pm = row["pm"]
    now = datetime.utcnow()
    log = MedicationLog(patient_medication_id=pm.patient_medication_id, schedule_id=schedule_id,
                        scheduled_time=row["scheduled"], logged_by_caregiver_id=caregiver_id)
    if action == "taken":
        log.status = "late" if now > row["scheduled"] + LATE_AFTER else "taken"
        log.taken_at = now
        log.quantity_taken = pm.quantity_per_dose
    else:
        log.status = "missed"
    db.session.add(log)
    db.session.commit()

    evaluate_medication_log(log)
    refresh_insights(patient)
    return log, None


def remember_for_undo(log, message):
    """Offer Undo for this log (shown once, on the next page)."""
    session["undo_dose"] = {"log_id": log.log_id, "message": message}


def undo_once():
    """The "Undo" bar is shown once, on the first page opened right after a dose was logged."""
    undo = session.get("undo_dose")
    if undo and not undo.get("shown"):
        session["undo_dose"] = {**undo, "shown": True}
        return undo
    return None


def take_undo_log():
    """The dose log waiting to be undone (removed from the session), or None."""
    undo = session.pop("undo_dose", None)
    return db.session.get(MedicationLog, undo["log_id"]) if undo else None


def can_still_undo(log):
    return log is not None and datetime.utcnow() - log.timestamp <= UNDO_WINDOW


def delete_dose_log(log, patient):
    """Undo: delete the log + every alert it raised (per-dose and pattern), refresh insights."""
    Alert.query.filter(Alert.medication_log_id == log.log_id).delete(synchronize_session=False)
    db.session.delete(log)
    db.session.commit()
    refresh_insights(patient)
