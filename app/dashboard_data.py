"""
Shared dashboard data: Today's doses and the stat-card numbers. Used by the
Patient, Caregiver and HCP dashboards so all three show exactly the same figures.
"""
from datetime import datetime, time, timedelta

from flask import current_app
from sqlalchemy import or_

from app.models import PatientMedication, MedicationLog, SymptomRecord, Alert, Insight

LATE_AFTER = timedelta(minutes=30)    # taken more than 30 min after the scheduled time = "late"
DUE_WINDOW = timedelta(minutes=30)    # a dose shows "Due now" from 30 min before its time
STATS_WINDOW = timedelta(days=7)      # stat cards count missed doses / alerts over the last 7 days


def todays_doses(patient_id):
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


def patient_stats(patient_id, doses):
    """Numbers for the row of stat cards at the top of a dashboard."""
    since = datetime.utcnow() - STATS_WINDOW
    return {
        "doses_total": len(doses),
        "doses_done": sum(1 for d in doses if d["state"] in ("taken", "late")),
        "missed_7d": (
            MedicationLog.query.join(PatientMedication)
            .filter(PatientMedication.patient_id == patient_id,
                    MedicationLog.status == "missed",
                    MedicationLog.timestamp >= since)
            .count()
        ),
        "latest_symptom": (
            SymptomRecord.query.filter_by(patient_id=patient_id)
            .order_by(SymptomRecord.timestamp.desc()).first()
        ),
        "alerts_7d": Alert.query.filter(Alert.patient_id == patient_id, Alert.timestamp >= since).count(),
    }


def patient_overview(patient_id):
    """Everything the shared dashboard layout needs for one patient."""
    doses = todays_doses(patient_id)
    return {
        "doses": doses,
        "stats": patient_stats(patient_id, doses),
        "symptom_records": (
            SymptomRecord.query.filter_by(patient_id=patient_id)
            .order_by(SymptomRecord.timestamp.desc()).limit(10).all()
        ),
        "alerts": (
            Alert.query.filter_by(patient_id=patient_id)
            .order_by(Alert.timestamp.desc()).limit(10).all()
        ),
        "insights": (
            Insight.query.filter_by(patient_id=patient_id)
            .order_by(Insight.generated_at.desc()).limit(5).all()
        ),
    }
