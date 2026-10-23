"""
Rule-Based Engine -- evaluates symptom/medication entries against fixed
default thresholds and generates Alert records. Thresholds are hardcoded
constants (Admin's "Configure thresholds" function was dropped per the
supervisor-approved scope reduction -- see Chapter 4 revision notes).
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


def evaluate_symptom_entry(symptom_record: SymptomRecord):
    """Called right after a SymptomRecord is stored.

    Rule 1: severity >= 3 (Moderate) raises an alert, e.g. "Moderate symptom: Rigidity 3/4".
    Timestamped with the record's time, so seed_demo.py can replay old entries.
    """
    if symptom_record.severity < SEVERITY_ALERT_THRESHOLD:
        return None
    alert = Alert(
        patient_id=symptom_record.patient_id,
        rule_triggered=(f"{symptom_record.severity_label()} symptom: "
                        f"{symptom_record.symptom_type.name} {symptom_record.severity}/{SEVERITY_MAX}"),
        timestamp=symptom_record.timestamp,
    )
    db.session.add(alert)
    db.session.commit()
    return alert


def evaluate_medication_log(log: MedicationLog):
    """Called right after a MedicationLog entry is stored. Returns the alerts raised (may be empty).

    Rule 2: every missed dose raises its own alert.
    Rule 3: >= 3 missed doses in 7 days raises an extra pattern alert, unless the
            patient already got one in the last 7 days (avoids duplicates).
    Alerts are timestamped with the log's time, so seed_demo.py can replay old logs.
    """
    if log.status != "missed":
        return []
    pm = log.patient_medication
    alerts = [Alert(patient_id=pm.patient_id, rule_triggered=_missed_dose_text(log),
                    timestamp=log.timestamp)]

    window_start = log.timestamp - MISSED_PATTERN_WINDOW
    missed_count = (
        MedicationLog.query.join(PatientMedication)
        .filter(PatientMedication.patient_id == pm.patient_id,
                MedicationLog.status == "missed",
                MedicationLog.timestamp > window_start,
                MedicationLog.timestamp <= log.timestamp)
        .count()
    )
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
        ))

    db.session.add_all(alerts)
    db.session.commit()
    return alerts


def _missed_dose_text(log):
    """e.g. "Missed dose: Pramipexole 0.25 mg at 13:00"."""
    text = f"Missed dose: {log.patient_medication.dose_label()}"
    if log.schedule:
        text += f" at {log.schedule.dose_time.strftime('%H:%M')}"
    return text
