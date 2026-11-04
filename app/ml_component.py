"""
ML Component -- system actor that turns each patient's own logs into Insights:
patterns over weeks to discuss with the doctor, never a diagnosis. (Alerts from
rule_engine.py are the "act now" side; the two stay separate -- see CLAUDE.md.)

Run with:  flask --app run analyze   (rebuilds every patient's Insights)

Features (ML Phase 3):
  1. Trend detection: per symptom, a linear regression over the last 30 days gives
     rising / stable / improving. A rising symptom is compared with typical PD
     progression from the PPMI baseline (analysis/progression.py).
  2. Compared to typical: the symptom's recent average vs PPMI's typical range
     (25th-75th percentile) for people at the same stage, i.e. years since diagnosis
     (analysis/severity_baseline.py). Needs the patient's diagnosis year.
  3. Wearing-off: is a symptom worse when logged long after the last dose than soon
     after it? Uses only the patient's own symptom and dose logs.
  4. Missed doses vs symptoms: is a symptom higher in the 24 hours after a missed dose than
     at other times the same week? Patient's own data only.

Each symptom gets at most ONE insight combining what features 1 and 2 found, so the
Insights card stays short. The PPMI baseline is read from PPMI_DATA_DIR/output
(aggregated tables only, kept outside the repo under the DUA). Without it, insights
still work, just without the PPMI comparisons.
"""
from bisect import bisect_right
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from flask import current_app
from sklearn.linear_model import LinearRegression

from app.extensions import db
from app.models import (
    Patient, PatientMedication, MedicationLog, SymptomRecord, Insight, SEVERITY_LABELS, SEVERITY_MAX,
)

TREND_WINDOW_DAYS = 30   # look at the last 30 days of entries
MIN_ENTRIES = 4          # a trend needs at least this many entries for a symptom...
MIN_SPAN_DAYS = 7        # ...spread over at least a week
CHANGE_THRESHOLD = 0.5   # change across the window (score points) that counts as rising / improving
MIN_LEVEL_ENTRIES = 3    # entries needed to compare a symptom's average with the typical range
ABOVE_MARGIN = 0.5       # how far above the typical range (points) before it's worth saying
SOON_AFTER_HOURS = 2     # wearing-off: logged within 2 h of the last dose = "soon after a dose"
LATER_AFTER_HOURS = 3    # logged 3+ h after the last dose = "later after a dose"
NO_DOSE_HOURS = 12       # no dose logged in the 12 h before an entry: leave that entry out
MIN_EACH_GROUP = 3       # entries needed in both groups
WEARING_OFF_GAP = 0.75   # "later" average must be this much higher (score points)
AFTER_MISS_HOURS = 24    # missed doses: entries within 24 h after a missed dose's time = "after a miss"
SAME_WEEK_DAYS = 3       # "normal" entries compared against: within 3 days of a missed dose (same week),
                         # so a symptom that is simply rising over the month doesn't look like a miss effect
MIN_MISSES = 2           # the "after a miss" entries must come from at least 2 different missed doses
MISSED_DOSE_GAP = 0.75   # "after a miss" average must be this much higher (score points)

# Same stages as analysis/ppmi_data.py: years since diagnosis
STAGES = [(0, 3, "0-2 years"), (3, 6, "3-5 years"), (6, None, "6+ years")]


# ---------------------------------------------------------------------------
# PPMI baseline (aggregated tables from the analysis/ scripts)
# ---------------------------------------------------------------------------

def _baseline_table(filename):
    ppmi_dir = current_app.config.get("PPMI_DATA_DIR")
    path = Path(ppmi_dir) / "output" / filename if ppmi_dir else None
    return pd.read_csv(path) if path and path.exists() else None


def load_baselines():
    """{"progression": {code: typical change per year},
        "severity": {(code, stage): (p25, median, p75)}}, empty dicts if the files aren't there."""
    progression, severity = {}, {}
    table = _baseline_table("progression_baseline.csv")
    if table is not None:
        overall = table[table["stage"] == "all"]
        progression = dict(zip(overall["code"], overall["mean"]))
    table = _baseline_table("severity_baseline.csv")
    if table is not None:
        severity = {(r.code, r.stage): (r.p25, r.median, r.p75) for r in table.itertuples()}
    return {"progression": progression, "severity": severity}


def stage_for(years):
    """4 -> "3-5 years" (None if unknown)."""
    if years is None or years < 0:
        return None
    return next(label for low, high, label in STAGES if years >= low and (high is None or years < high))


# ---------------------------------------------------------------------------
# Feature 1: trend detection
# ---------------------------------------------------------------------------

def symptom_trend(entries, window_start):
    """Fit a straight line through one symptom's scores (score vs days).
    entries: list of (timestamp, severity). Returns None if there isn't enough data,
    else a dict with the fitted start/end level and the change across the period."""
    if len(entries) < MIN_ENTRIES:
        return None
    days = np.array([(ts - window_start).total_seconds() / 86400 for ts, _ in entries])
    scores = np.array([sev for _, sev in entries], dtype=float)
    if days.max() - days.min() < MIN_SPAN_DAYS:
        return None

    model = LinearRegression().fit(days.reshape(-1, 1), scores)
    slope_per_day = float(model.coef_[0])
    start, end = (float(np.clip(v, 0, SEVERITY_MAX)) for v in model.predict([[days.min()], [days.max()]]))
    change = end - start
    if change >= CHANGE_THRESHOLD:
        direction = "rising"
    elif change <= -CHANGE_THRESHOLD:
        direction = "improving"
    else:
        direction = "stable"
    return {"direction": direction, "start": start, "end": end, "change": change,
            "per_year": slope_per_day * 365, "entries": len(entries),
            "span_days": int(round(days.max() - days.min()))}


def _compared_with_progression(trend, typical):
    """The sentence comparing a rising symptom with typical PD progression in PPMI."""
    if typical is None:
        return "Worth mentioning at the next appointment."
    if abs(typical) < 0.02:
        return ("In the PPMI study this usually stays about the same over the years, "
                "so the change is worth discussing with the doctor.")
    if trend["per_year"] > 3 * typical:
        return (f"Much faster than typical for Parkinson's (PPMI: about {typical:+.2f} a year), "
                f"so worth discussing with the doctor.")
    return "Worth mentioning at the next appointment."


# ---------------------------------------------------------------------------
# Feature 2: compared to typical for the patient's stage
# ---------------------------------------------------------------------------

def compare_with_typical(entries, typical_range):
    """Recent average vs PPMI's typical range (p25-p75) for the same stage.
    Returns None if there isn't enough data or no baseline, else a dict with the
    average, the range and whether it's "above" / "within" / "below" it."""
    if typical_range is None or len(entries) < MIN_LEVEL_ENTRIES:
        return None
    p25, _, p75 = typical_range
    average = float(np.mean([sev for _, sev in entries]))
    if average > p75 + ABOVE_MARGIN:
        position = "above"
    elif average < p25:
        position = "below"
    else:
        position = "within"
    return {"average": average, "p25": p25, "p75": p75, "position": position}


# ---------------------------------------------------------------------------
# Feature 3: wearing-off
# ---------------------------------------------------------------------------

def dose_times(patient, since):
    """When the patient actually took a dose (Taken or Late logs, any medication), sorted."""
    logs = (MedicationLog.query.join(PatientMedication)
            .filter(PatientMedication.patient_id == patient.patient_id,
                    MedicationLog.status.in_(["taken", "late"]),
                    MedicationLog.taken_at >= since)
            .all())
    return sorted(log.taken_at for log in logs)


def wearing_off(entries, doses):
    """Compare one symptom's scores soon after a dose with its scores later after a dose.
    entries: [(timestamp, severity)], doses: sorted taken_at times. Returns None, or a dict
    if the symptom is clearly worse later after a dose (a possible wearing-off pattern)."""
    soon, later = [], []
    for ts, severity in entries:
        i = bisect_right(doses, ts)                       # doses before this entry
        if i == 0:
            continue
        hours = (ts - doses[i - 1]).total_seconds() / 3600
        if hours < SOON_AFTER_HOURS:
            soon.append(severity)
        elif LATER_AFTER_HOURS <= hours <= NO_DOSE_HOURS:
            later.append(severity)
    if len(soon) < MIN_EACH_GROUP or len(later) < MIN_EACH_GROUP:
        return None
    soon_avg, later_avg = float(np.mean(soon)), float(np.mean(later))
    if later_avg - soon_avg < WEARING_OFF_GAP:
        return None
    return {"soon": soon_avg, "later": later_avg, "n_soon": len(soon), "n_later": len(later)}


# ---------------------------------------------------------------------------
# Feature 4: missed doses vs symptoms
# ---------------------------------------------------------------------------

def missed_dose_times(patient, since):
    """Scheduled times of the patient's Missed doses (current status, so a Missed that was
    corrected to Taken doesn't count; Skipped doesn't count either), sorted."""
    logs = (MedicationLog.query.join(PatientMedication)
            .filter(PatientMedication.patient_id == patient.patient_id,
                    MedicationLog.status == "missed",
                    MedicationLog.scheduled_time.isnot(None),
                    MedicationLog.scheduled_time >= since)
            .all())
    return sorted(log.scheduled_time for log in logs)


def after_missed_doses(entries, misses):
    """Compare one symptom's scores in the 24 h after a missed dose with its other scores from
    the same week. entries: [(timestamp, severity)], misses: sorted scheduled times of missed
    doses. Returns None, or a dict if the symptom is clearly higher after missed doses."""
    after_window = timedelta(hours=AFTER_MISS_HOURS)
    same_week = timedelta(days=SAME_WEEK_DAYS)
    after, normal, misses_involved = [], [], set()
    for ts, severity in entries:
        recent = [m for m in misses[:bisect_right(misses, ts)] if ts - m <= after_window]
        if recent:
            after.append(severity)
            misses_involved.update(recent)
        elif any(abs(ts - m) <= same_week for m in misses):
            normal.append(severity)
    if (len(after) < MIN_EACH_GROUP or len(normal) < MIN_EACH_GROUP
            or len(misses_involved) < MIN_MISSES):
        return None
    after_avg, normal_avg = float(np.mean(after)), float(np.mean(normal))
    if after_avg - normal_avg < MISSED_DOSE_GAP:
        return None
    return {"after": after_avg, "normal": normal_avg, "n_after": len(after), "n_normal": len(normal),
            "misses": len(misses_involved)}


# ---------------------------------------------------------------------------
# Putting it together: one insight per symptom, most important first
# ---------------------------------------------------------------------------

def _level(value):
    """1.4 -> 'Slight' (nearest label on the 0-4 scale)."""
    return SEVERITY_LABELS[int(round(min(max(value, 0), SEVERITY_MAX)))]


def _from_to(trend):
    """'from about 1.2 to 2.4 (Slight → Mild)', or '(Mild)' when the label doesn't change."""
    start, end = _level(trend["start"]), _level(trend["end"])
    labels = start if start == end else f"{start} → {end}"
    return f"from about {trend['start']:.1f} to {trend['end']:.1f} ({labels})"


def build_insights(patient, now, baselines):
    window_start = now - timedelta(days=TREND_WINDOW_DAYS)
    records = (SymptomRecord.query
               .filter(SymptomRecord.patient_id == patient.patient_id,
                       SymptomRecord.timestamp >= window_start)
               .order_by(SymptomRecord.timestamp).all())
    by_symptom = {}
    for r in records:
        by_symptom.setdefault(r.symptom_type, []).append((r.timestamp, r.severity))

    stage = stage_for(patient.years_since_diagnosis())
    doses = dose_times(patient, window_start - timedelta(hours=NO_DOSE_HOURS))
    misses = missed_dose_times(patient, window_start - timedelta(hours=AFTER_MISS_HOURS))
    rising, wearing, after_miss, above, improving = [], [], [], [], []   # (sort key, text)
    stable, within = [], []                 # symptom names for the two summary lines

    for symptom, entries in by_symptom.items():
        trend = symptom_trend(entries, window_start)
        level = compare_with_typical(entries, baselines["severity"].get((symptom.ppmi_code, stage)))
        wear = wearing_off(entries, doses)
        wear_text = ""
        if wear:
            wear_text = (f" It also tends to be worse later after a dose ({wear['later']:.1f} when logged "
                         f"{LATER_AFTER_HOURS}+ hours after, vs {wear['soon']:.1f} within {SOON_AFTER_HOURS} hours), "
                         f"which can be a sign of \"wearing-off\".")
        miss = after_missed_doses(entries, misses)
        miss_text = ""
        if miss:
            miss_text = (f" It also tends to be higher in the {AFTER_MISS_HOURS} hours after a missed dose "
                         f"({miss['after']:.1f} vs {miss['normal']:.1f} at other times that week).")
        level_text = ""
        if level and level["position"] == "above":
            level_text = (f" Its recent average ({level['average']:.1f}) is above the typical range for "
                          f"people {stage} after diagnosis in PPMI ({level['p25']:.1f}–{level['p75']:.1f}).")

        if trend and trend["direction"] == "rising":
            text = (f"{symptom.name}: getting worse over the past {trend['span_days']} days, "
                    f"{_from_to(trend)}, {trend['entries']} entries.{level_text}{wear_text}{miss_text} "
                    + _compared_with_progression(trend, baselines["progression"].get(symptom.ppmi_code)))
            rising.append((trend["change"], text))
        elif level and level["position"] == "above":
            days = (entries[-1][0] - entries[0][0]).days
            above.append((level["average"] - level["p75"],
                          f"{symptom.name}: averaging {level['average']:.1f} ({_level(level['average'])}) "
                          f"over the past {days} days, above the typical range for people {stage} after "
                          f"diagnosis in PPMI ({level['p25']:.1f}–{level['p75']:.1f}).{wear_text}{miss_text} "
                          f"Worth mentioning at the next appointment."))
        elif wear:
            wearing.append((wear["later"] - wear["soon"],
                            f"{symptom.name}: tends to be worse later after a dose, averaging "
                            f"{wear['later']:.1f} when logged {LATER_AFTER_HOURS}+ hours after a dose vs "
                            f"{wear['soon']:.1f} within {SOON_AFTER_HOURS} hours ({wear['n_later']} and "
                            f"{wear['n_soon']} entries). This can be a sign of \"wearing-off\", "
                            f"so worth discussing with the doctor.{miss_text}"))
        elif miss:
            after_miss.append((miss["after"] - miss["normal"],
                               f"{symptom.name}: tends to be higher in the {AFTER_MISS_HOURS} hours after a "
                               f"missed dose, averaging {miss['after']:.1f} vs {miss['normal']:.1f} at other "
                               f"times the same week ({miss['n_after']} and {miss['n_normal']} entries, after "
                               f"{miss['misses']} missed doses). Worth discussing with the doctor."))
        elif trend and trend["direction"] == "improving":
            improving.append((trend["change"],
                              f"{symptom.name}: improving over the past {trend['span_days']} days, "
                              f"{_from_to(trend)}, {trend['entries']} entries."))
        else:
            if trend:  # stable
                stable.append(symptom.name)
            if level and level["position"] != "above":
                within.append(symptom.name)

    texts = [t for _, t in sorted(rising, reverse=True)]     # biggest rise first
    texts += [t for _, t in sorted(wearing, reverse=True)]   # clearest wearing-off pattern first
    texts += [t for _, t in sorted(after_miss, reverse=True)]  # clearest missed-dose effect first
    texts += [t for _, t in sorted(above, reverse=True)]     # furthest above typical first
    texts += [t for _, t in sorted(improving)]               # biggest improvement first
    # Summary lines: symptoms that are both stable and within the typical range share one line
    both = sorted(set(stable) & set(within))
    stable_only = sorted(set(stable) - set(both))
    within_only = sorted(set(within) - set(both))
    if both:
        texts.append(f"Stable and within the typical range for people {stage} after diagnosis: "
                     f"{', '.join(both)}.")
    if stable_only:
        texts.append(f"Stable recently: {', '.join(stable_only)}.")
    if within_only:
        texts.append(f"Within or below the typical range for people {stage} after diagnosis: "
                     f"{', '.join(within_only)}.")
    return texts


# ---------------------------------------------------------------------------
# Run for every patient
# ---------------------------------------------------------------------------

def analyze_patient(patient, now=None, baselines=None):
    """Replace one patient's Insights with fresh ones. Returns how many were created."""
    now = now or datetime.utcnow()
    baselines = baselines if baselines is not None else load_baselines()
    texts = build_insights(patient, now, baselines)

    Insight.query.filter_by(patient_id=patient.patient_id).delete()
    for text in texts:
        db.session.add(Insight(patient_id=patient.patient_id, content=text, generated_at=now))
    db.session.commit()
    return len(texts)


def analyze_all_patients():
    """Rebuild Insights for every active patient.
    Returns (patients analysed, insights created, which baselines were found)."""
    baselines = load_baselines()
    now = datetime.utcnow()
    patients = Patient.query.filter_by(account_status="active").all()
    total = sum(analyze_patient(p, now, baselines) for p in patients)
    found = [name for name, table in baselines.items() if table]
    return len(patients), total, found
