"""
Shared dashboard data: Today's doses, the stat-card numbers and the symptom trend
chart. Used by the Patient, Caregiver and HCP dashboards so all three show exactly
the same figures.
"""
from collections import defaultdict
from datetime import datetime, time, timedelta
from statistics import mean

from flask import current_app
from sqlalchemy import func, or_

from app.models import (
    PatientMedication, MedicationLog, SymptomRecord, Alert, Insight, SEVERITY_LABELS, SEVERITY_MAX,
)
from app.rule_engine import SEVERITY_ALERT_THRESHOLD

LATE_AFTER = timedelta(minutes=30)    # taken more than 30 min after the scheduled time = "late"
DUE_WINDOW = timedelta(minutes=30)    # a dose shows "Due now" from 30 min before its time
STATS_WINDOW = timedelta(days=7)      # stat cards count missed doses / alerts over the last 7 days
TREND_DAYS = 14                       # the symptom trend chart covers the last 14 days
CORRECTION_DAYS = 3                   # doses can be corrected for today + the 2 days before
HISTORY_DAYS = 7                      # caregiver / HCP pages show the last 7 days of dose logs


def _utc_offset():
    return timedelta(hours=current_app.config["DISPLAY_UTC_OFFSET_HOURS"])


def local_today():
    """Today's date in local time (UTC+3)."""
    return (datetime.utcnow() + _utc_offset()).date()


def local_date(utc_dt):
    """The local date of a stored UTC datetime."""
    return (utc_dt + _utc_offset()).date()


def dose_day(log):
    """The local day a dose belongs to: its scheduled time, or when an extra dose was taken."""
    return local_date(log.scheduled_time or log.taken_at or log.timestamp)


def can_correct_dose(log):
    """Doses can be corrected if they belong to today or the 2 days before (local dates)."""
    return 0 <= (local_today() - dose_day(log)).days < CORRECTION_DAYS


def can_edit_symptom(record):
    """A patient can edit or delete their OWN symptom entries on the (local) day they were logged.
    Entries a caregiver logged for them ("Logged by Grace") are the caregiver's report: not editable."""
    return record.recorded_by_caregiver_id is None and local_date(record.timestamp) == local_today()


def recent_dose_logs(patient_id, days=CORRECTION_DAYS):
    """The patient's dose logs from today and the days before (newest first), i.e. the ones
    they can still correct. Extra doses count by the time they were taken."""
    since = datetime.combine(local_today() - timedelta(days=days - 1), time.min) - _utc_offset()
    when = func.coalesce(MedicationLog.scheduled_time, MedicationLog.taken_at, MedicationLog.timestamp)
    return (MedicationLog.query.join(PatientMedication)
            .filter(PatientMedication.patient_id == patient_id, when >= since)
            .order_by(when.desc()).all())


def current_medications(patient_id):
    """Medications the patient is on today (started, not ended), by name."""
    today = local_today()
    meds = PatientMedication.query.filter(
        PatientMedication.patient_id == patient_id,
        PatientMedication.started_date <= today,
        or_(PatientMedication.end_date.is_(None), PatientMedication.end_date >= today),
    ).all()
    return sorted(meds, key=lambda pm: pm.dose_label())


def dose_summary(logs):
    """Counts for the dose history header, e.g. {"taken": 18, "missed": 3, "skipped": 1}.
    Late counts as taken; extra doses aren't counted (they weren't scheduled)."""
    counts = {"taken": 0, "missed": 0, "skipped": 0}
    for log in logs:
        if log.schedule_id is not None:
            counts["taken" if log.status in ("taken", "late") else log.status] += 1
    return counts


def todays_doses(patient_id):
    """One row per scheduled dose today, oldest first, with its log (if any) and a state:
    taken / late / missed / skipped (logged) or due / upcoming (not logged yet)."""
    offset = _utc_offset()
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


def symptom_trends(patient_id, days=TREND_DAYS):
    """Data for the symptom trend chart: each symptom's daily average score (0-4) over
    the last `days` local days. Days with no entry are None, so the chart only puts a
    dot on days the patient actually logged something."""
    offset = _utc_offset()
    today = (datetime.utcnow() + offset).date()
    day_list = [today - timedelta(days=days - 1 - i) for i in range(days)]
    since = datetime.combine(day_list[0], time.min) - offset  # local midnight, in UTC

    scores = defaultdict(lambda: defaultdict(list))  # symptom name -> local day -> [scores]
    records = SymptomRecord.query.filter(SymptomRecord.patient_id == patient_id,
                                         SymptomRecord.timestamp >= since)
    for r in records:
        scores[r.symptom_type.name][(r.timestamp + offset).date()].append(r.severity)

    series = []
    for name, by_day in scores.items():
        counts = [len(by_day.get(d, [])) for d in day_list]
        series.append({
            "name": name,
            "values": [round(mean(by_day[d]), 2) if d in by_day else None for d in day_list],
            "counts": counts,  # tooltip says "average of N entries" when a day has several
            "entries": sum(counts),
        })
    series.sort(key=lambda s: (-s["entries"], s["name"]))  # most-logged symptom is shown first

    return {
        "labels": [d.strftime("%d %b") for d in day_list],
        "series": series,
        "days": days,
        "threshold": SEVERITY_ALERT_THRESHOLD,
        "max": SEVERITY_MAX,
        "severity_labels": SEVERITY_LABELS,
    }


def patient_overview(patient_id):
    """Everything the shared dashboard layout needs for one patient."""
    doses = todays_doses(patient_id)
    history = recent_dose_logs(patient_id, days=HISTORY_DAYS)
    return {
        # Caregiver / HCP pages: what they're on, and how the last week of doses went
        # (incl. "Corrected" badges, so a fixed mistake from yesterday is visible to them too)
        "medications": current_medications(patient_id),
        "dose_history": history,
        "dose_summary": dose_summary(history),
        "history_days": HISTORY_DAYS,
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
            # Newest run first; within a run, in the order the ML component ranked them
            Insight.query.filter_by(patient_id=patient_id)
            .order_by(Insight.generated_at.desc(), Insight.insight_id).limit(10).all()
        ),
        "trends": symptom_trends(patient_id),
    }
