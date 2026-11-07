"""
Rule-Based Engine -- evaluates symptom/medication entries against fixed
default thresholds and generates Alert records. Thresholds are hardcoded
constants (Admin's "Configure thresholds" function was dropped per the
supervisor-approved scope reduction -- see Chapter 4 revision notes).

Every alert records the dose log / symptom entry that raised it (medication_log_id /
symptom_record_id), so Undo, dose corrections and symptom edits can re-run the rules
and remove exactly the alerts that no longer apply.
"""
from datetime import timedelta

from app.extensions import db
from app.models import Alert, SymptomRecord, MedicationLog, PatientMedication, SEVERITY_MAX

# Default thresholds (documented assumption -- future work: make these
# clinically validated rather than illustrative placeholders)
SEVERITY_ALERT_THRESHOLD = 3          # 3 = Moderate on the 0-4 MDS-UPDRS scale
# Medication: every dose logged as "missed" raises an alert, plus a pattern alert
# when MISSED_PATTERN_COUNT doses are missed within MISSED_PATTERN_WINDOW.
MISSED_PATTERN_COUNT = 3
MISSED_PATTERN_WINDOW = timedelta(days=7)
PATTERN_ALERT_PREFIX = "Missed dose pattern"
# Statuses that raise a per-dose alert. Skipped (left out on purpose) alerts too, so caregivers
# and the HCP see it, but only MISSED doses count towards the pattern alert (decided 7 Nov).
DOSE_ALERT_STATUSES = ("missed", "skipped")


# ---------------------------------------------------------------------------
# Rule 1: symptom severity
# ---------------------------------------------------------------------------

def evaluate_symptom_entry(symptom_record: SymptomRecord):
    """Called right after a SymptomRecord is stored.

    Rule 1: severity >= 3 (Moderate) raises an alert, e.g. "Moderate symptom: Tremor 3/4".
    Timestamped with the record's time, so seed_demo.py can replay old entries.
    """
    if symptom_record.severity < SEVERITY_ALERT_THRESHOLD:
        return None
    alert = Alert(
        patient_id=symptom_record.patient_id,
        rule_triggered=(f"{symptom_record.severity_label()} symptom: "
                        f"{symptom_record.symptom_type.name} {symptom_record.severity}/{SEVERITY_MAX}"),
        timestamp=symptom_record.timestamp,
        symptom_record_id=symptom_record.record_id,
    )
    db.session.add(alert)
    db.session.commit()
    return alert


def on_symptom_edited(symptom_record: SymptomRecord):
    """Re-run rule 1 after the patient edited an entry's score: remove the entry's old alert
    and evaluate again. Covers >= 3 -> < 3 (alert removed), < 3 -> >= 3 (alert added) and
    3 <-> 4 (alert text updated)."""
    _delete_alerts(Alert.symptom_record_id == symptom_record.record_id)
    db.session.commit()
    return evaluate_symptom_entry(symptom_record)


def on_symptom_deleted(symptom_record: SymptomRecord):
    """Remove the alert an entry raised. Call BEFORE deleting the entry itself."""
    _delete_alerts(Alert.symptom_record_id == symptom_record.record_id)


# ---------------------------------------------------------------------------
# Rules 2 + 3: missed doses
# ---------------------------------------------------------------------------

def evaluate_medication_log(log: MedicationLog):
    """Called right after a MedicationLog entry is stored. Returns the alerts raised (may be empty).

    Rule 2: every missed dose raises its own alert; so does every skipped dose ("Skipped dose: ...").
    Rule 3: >= 3 MISSED doses in 7 days raises an extra pattern alert, unless the
            patient already got one in the last 7 days (avoids duplicates). Skipped doses
            don't count: they were left out on purpose.
    Alerts are timestamped with the log's time, so seed_demo.py can replay old logs.
    """
    if log.status not in DOSE_ALERT_STATUSES:
        return []
    pm = log.patient_medication
    alerts = [_dose_alert(log)]
    if log.status != "missed":          # skipped: its own alert only, no pattern check
        db.session.add_all(alerts)
        db.session.commit()
        return alerts

    window_start = log.timestamp - MISSED_PATTERN_WINDOW
    missed_count = _missed_in_window(pm.patient_id, log.timestamp)
    already_alerted = Alert.query.filter(
        Alert.patient_id == pm.patient_id,
        Alert.rule_triggered.startswith(PATTERN_ALERT_PREFIX),
        Alert.timestamp > window_start,
    ).first()
    if missed_count >= MISSED_PATTERN_COUNT and not already_alerted:
        alerts.append(Alert(
            patient_id=pm.patient_id,
            rule_triggered=f"{PATTERN_ALERT_PREFIX}: {missed_count} missed doses in the last 7 days",
            timestamp=log.timestamp,
            medication_log_id=log.log_id,
        ))

    db.session.add_all(alerts)
    db.session.commit()
    return alerts


def on_dose_corrected(log: MedicationLog, old_status: str):
    """Re-run rules 2 + 3 after the patient corrected a dose's status.
    Returns (alerts removed, alerts created).

    - was Missed / Skipped: delete that dose's own alert.
    - now Missed / Skipped: add its own alert, as a normal tap would ("Missed dose" / "Skipped dose").
    - if Missed is involved, refresh the pattern alerts around this dose (refresh_pattern_alerts).
    - Taken <-> Late, or no status change: nothing to do.
    """
    involved = {old_status, log.status} & set(DOSE_ALERT_STATUSES)
    if old_status == log.status or not involved:
        return 0, []
    removed, created = 0, []
    if old_status in DOSE_ALERT_STATUSES:
        removed += _delete_alerts(Alert.medication_log_id == log.log_id,
                                  ~Alert.rule_triggered.startswith(PATTERN_ALERT_PREFIX))
    if log.status in DOSE_ALERT_STATUSES:
        alert = _dose_alert(log)
        db.session.add(alert)
        created.append(alert)
    if "missed" in involved:
        pruned, patterns = refresh_pattern_alerts(log.patient_medication.patient_id, log.timestamp)
        removed += pruned
        created += patterns
    db.session.commit()
    return removed, created


def refresh_pattern_alerts(patient_id, around):
    """Make rule 3 true again after a correction around time `around`:
    1. delete pattern alerts whose own 7-day window no longer has >= 3 missed doses;
    2. raise a pattern alert for any missed dose (within 7 days either side) whose window now
       has >= 3, unless there's already a pattern alert within 7 days of it (no duplicates).
    A corrected dose can sit in the MIDDLE of a run of misses, so both steps are needed.
    Returns (alerts deleted, alerts created)."""
    db.session.flush()
    stale = [a for a in Alert.query.filter(Alert.patient_id == patient_id,
                                           Alert.rule_triggered.startswith(PATTERN_ALERT_PREFIX))
             if _missed_in_window(patient_id, a.timestamp) < MISSED_PATTERN_COUNT]
    for alert in stale:
        db.session.delete(alert)
    db.session.flush()

    created = []
    nearby_misses = (MedicationLog.query.join(PatientMedication)
                     .filter(PatientMedication.patient_id == patient_id,
                             MedicationLog.status == "missed",
                             MedicationLog.timestamp > around - MISSED_PATTERN_WINDOW,
                             MedicationLog.timestamp <= around + MISSED_PATTERN_WINDOW)
                     .order_by(MedicationLog.timestamp).all())
    for missed in nearby_misses:
        count = _missed_in_window(patient_id, missed.timestamp)
        if count < MISSED_PATTERN_COUNT:
            continue
        already = Alert.query.filter(Alert.patient_id == patient_id,
                                     Alert.rule_triggered.startswith(PATTERN_ALERT_PREFIX),
                                     Alert.timestamp > missed.timestamp - MISSED_PATTERN_WINDOW,
                                     Alert.timestamp < missed.timestamp + MISSED_PATTERN_WINDOW).first()
        if already:
            continue
        alert = Alert(patient_id=patient_id, timestamp=missed.timestamp, medication_log_id=missed.log_id,
                      rule_triggered=f"{PATTERN_ALERT_PREFIX}: {count} missed doses in the last 7 days")
        db.session.add(alert)
        db.session.flush()  # so the next dose in the loop sees it and doesn't duplicate it
        created.append(alert)
    return len(stale), created


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _missed_in_window(patient_id, window_end):
    """Missed doses logged in the 7 days up to window_end."""
    return (
        MedicationLog.query.join(PatientMedication)
        .filter(PatientMedication.patient_id == patient_id,
                MedicationLog.status == "missed",
                MedicationLog.timestamp > window_end - MISSED_PATTERN_WINDOW,
                MedicationLog.timestamp <= window_end)
        .count()
    )


def _delete_alerts(*conditions):
    """Delete the alerts matching all conditions. Returns how many were deleted."""
    return Alert.query.filter(*conditions).delete(synchronize_session=False)


def _dose_alert(log):
    """The per-dose alert for a missed or skipped dose, linked to its log."""
    return Alert(patient_id=log.patient_medication.patient_id, rule_triggered=_dose_alert_text(log),
                 timestamp=log.timestamp, medication_log_id=log.log_id)


def _dose_alert_text(log):
    """e.g. "Missed dose: Pramipexole 0.25 mg at 13:00" or "Skipped dose: ..."."""
    text = f"{log.status.capitalize()} dose: {log.patient_medication.dose_label()}"
    if log.schedule:
        text += f" at {log.schedule.dose_time.strftime('%H:%M')}"
    return text
