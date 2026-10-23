"""
Lightweight ML Component -- periodic background job that analyzes recent
symptom trends per patient and stores an Insight. Starts as a simple
moving-average/trend calculation (pandas), not a trained model, per the
project's "lightweight" scope. Intended to be run on a schedule (cron,
APScheduler, or a manual CLI command for the demo) -- not called live
from any request.
"""
import pandas as pd
from datetime import datetime
from app.extensions import db
from app.models import Patient, SymptomRecord, Insight, SEVERITY_MAX


def analyze_all_patients():
    """Run trend analysis for every patient with enough recent data."""
    for patient in Patient.query.all():
        _analyze_patient(patient)


def _analyze_patient(patient):
    records = (
        SymptomRecord.query.filter_by(patient_id=patient.patient_id)
        .order_by(SymptomRecord.timestamp.asc())
        .all()
    )
    if len(records) < 3:
        return  # not enough data yet to say anything meaningful

    df = pd.DataFrame({
        "timestamp": [r.timestamp for r in records],
        "severity": [r.severity for r in records],
    })
    recent_avg = df["severity"].tail(5).mean()
    overall_avg = df["severity"].mean()
    change = 0.5  # on the 0-4 scale; placeholder until the PPMI baseline replaces this

    if recent_avg > overall_avg + change:
        content = (
            f"Symptom severity trending upward: recent average "
            f"{recent_avg:.1f} vs overall average {overall_avg:.1f}."
        )
    elif recent_avg < overall_avg - change:
        content = (
            f"Symptom severity trending downward: recent average "
            f"{recent_avg:.1f} vs overall average {overall_avg:.1f}."
        )
    else:
        content = f"Symptom severity stable around {overall_avg:.1f}/{SEVERITY_MAX}."

    insight = Insight(patient_id=patient.patient_id, content=content,
                       generated_at=datetime.utcnow())
    db.session.add(insight)
    db.session.commit()
