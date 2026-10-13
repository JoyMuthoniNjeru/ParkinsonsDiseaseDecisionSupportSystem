"""
Rule-Based Engine -- evaluates symptom/medication entries against fixed
default thresholds and generates Alert records. Thresholds are hardcoded
constants (Admin's "Configure thresholds" function was dropped per the
supervisor-approved scope reduction -- see Chapter 4 revision notes).
"""
from app.extensions import db
from app.models import Alert, SymptomRecord, MedicationLog

# Default thresholds (documented assumption -- future work: make these
# clinically validated rather than illustrative placeholders)
SEVERITY_ALERT_THRESHOLD = 7          # on the 0-10 scale
# Medication: every dose logged as "missed" raises an alert.


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


def evaluate_medication_log(log: MedicationLog):
    """Called right after a MedicationLog entry is stored."""
    if log.adherence_status != "missed":
        return None
    pm = log.patient_medication
    alert = Alert(
        patient_id=pm.patient_id,
        rule_triggered=f"Missed dose: {pm.medication.name}",
    )
    db.session.add(alert)
    db.session.commit()
    return alert
