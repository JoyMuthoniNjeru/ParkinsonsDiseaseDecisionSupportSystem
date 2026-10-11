"""
Rule-Based Engine -- evaluates symptom/medication entries against fixed
default thresholds and generates Alert records. Thresholds are hardcoded
constants (Admin's "Configure thresholds" function was dropped per the
supervisor-approved scope reduction -- see Chapter 4 revision notes).
"""
from datetime import datetime, timedelta
from app.extensions import db
from app.models import Alert, SymptomRecord, MedicationLog, PatientMedication

# Default thresholds (documented assumption -- future work: make these
# clinically validated rather than illustrative placeholders)
SEVERITY_ALERT_THRESHOLD = 7          # on the 0-10 scale
MISSED_DOSE_WINDOW_DAYS = 7
MISSED_DOSE_ALERT_COUNT = 3            # 3+ missed doses in the window triggers an alert


def evaluate_symptom_entry(symptom_record: SymptomRecord):
    """Called right after a SymptomRecord is stored."""
    if symptom_record.severity >= SEVERITY_ALERT_THRESHOLD:
        alert = Alert(
            patient_id=symptom_record.patient_id,
            rule_triggered=f"High severity symptom reported ({symptom_record.severity}/10)",
        )
        db.session.add(alert)
        db.session.commit()
        return alert
    return None


def evaluate_medication_adherence(patient_medication: PatientMedication):
    """Called right after a MedicationLog entry is stored."""
    window_start = datetime.utcnow() - timedelta(days=MISSED_DOSE_WINDOW_DAYS)
    missed_count = (
        MedicationLog.query.filter(
            MedicationLog.patient_medication_id == patient_medication.patient_medication_id,
            MedicationLog.adherence_status == "missed",
            MedicationLog.timestamp >= window_start,
        ).count()
    )
    if missed_count >= MISSED_DOSE_ALERT_COUNT:
        alert = Alert(
            patient_id=patient_medication.patient_id,
            rule_triggered=f"{missed_count} missed doses in the last {MISSED_DOSE_WINDOW_DAYS} days",
        )
        db.session.add(alert)
        db.session.commit()
        return alert
    return None
