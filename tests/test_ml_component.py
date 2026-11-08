"""
ML Component (app/ml_component.py). Never reads PPMI: baselines are either passed in empty,
or a tiny MADE-UP baseline CSV is written to pytest's tmp_path.

  1. Trend detection     (symptom_trend)
  2. Compared to typical (compare_with_typical, stage_for, load_baselines)
  3. Wearing-off         (wearing_off)
  4. Missed doses        (after_missed_doses, missed_dose_times)
  + build_insights / analyze_patient putting them together
"""
from datetime import datetime, timedelta

from conftest import make_log, make_symptom
from app.extensions import db
from app.models import Insight
from app.ml_component import (
    after_missed_doses, missed_dose_times, build_insights, analyze_patient, symptom_trend,
    compare_with_typical, stage_for, wearing_off, load_baselines,
)

NO_BASELINES = {"progression": {}, "severity": {}}
T0 = datetime(2026, 10, 1, 10, 0)  # a fixed start time for made-up entries


def at(days, hours=0):
    return T0 + timedelta(days=days, hours=hours)


# ---------------------------------------------------------------------------
# Feature 1: trend detection
# ---------------------------------------------------------------------------

def test_rising_scores_are_detected():
    entries = [(at(d), s) for d, s in [(0, 1), (3, 1), (6, 2), (9, 2), (12, 3), (14, 3)]]
    trend = symptom_trend(entries, T0)
    assert trend["direction"] == "rising"
    assert trend["change"] > 2 and trend["entries"] == 6 and trend["span_days"] == 14


def test_flat_scores_are_stable():
    assert symptom_trend([(at(d), 2) for d in range(0, 20, 4)], T0)["direction"] == "stable"


def test_falling_scores_are_improving():
    entries = [(at(d), s) for d, s in [(0, 3), (5, 3), (10, 2), (15, 1), (20, 1)]]
    assert symptom_trend(entries, T0)["direction"] == "improving"


def test_a_one_off_bump_is_not_a_trend():
    entries = [(at(d), s) for d, s in [(0, 2), (4, 2), (7, 3), (10, 2), (14, 2)]]   # one bad day mid-way
    trend = symptom_trend(entries, T0)
    assert trend["direction"] == "stable"
    assert abs(trend["change"]) < 0.5


def test_too_few_entries_or_too_short_a_period_gives_no_trend():
    assert symptom_trend([(at(d), 3) for d in (0, 5, 10)], T0) is None             # only 3 entries
    assert symptom_trend([(at(0, h * 12), 1 + h % 2) for h in range(5)], T0) is None  # 5 entries in 2 days


# ---------------------------------------------------------------------------
# Feature 2: compared to typical for the patient's stage
# ---------------------------------------------------------------------------

def test_stage_from_years_since_diagnosis():
    assert [stage_for(y) for y in (None, -1, 0, 2, 3, 5, 6, 20)] == [
        None, None, "0-2 years", "0-2 years", "3-5 years", "3-5 years", "6+ years", "6+ years"]


def test_above_within_below_typical():
    typical = (0.5, 1.0, 1.5)                                       # p25, median, p75
    level = lambda scores: compare_with_typical([(at(i), s) for i, s in enumerate(scores)], typical)
    assert level([3, 3, 2])["position"] == "above"                  # 2.67 > 1.5 + 0.5
    assert level([2, 2, 1])["position"] == "within"                 # 1.67: above p75 but not by 0.5
    assert level([0, 0, 0])["position"] == "below"
    assert level([3, 3]) is None                                    # fewer than 3 entries
    assert compare_with_typical([(at(i), 3) for i in range(3)], None) is None   # no baseline


def _write_fake_baselines(folder):
    """Made-up numbers in the same format as analysis/*.py writes -- NOT PPMI data."""
    out = folder / "output"
    out.mkdir()
    (out / "severity_baseline.csv").write_text(
        "code,stage,p25,median,p75\nNP2TRMR,3-5 years,0.5,1.0,1.5\n", encoding="utf-8")
    (out / "progression_baseline.csv").write_text(
        "code,stage,mean\nNP2TRMR,all,0.1\n", encoding="utf-8")


def test_fake_baseline_is_loaded(app, tmp_path):
    _write_fake_baselines(tmp_path)
    app.config["PPMI_DATA_DIR"] = str(tmp_path)
    try:
        baselines = load_baselines()
    finally:
        app.config["PPMI_DATA_DIR"] = None
    assert baselines["severity"][("NP2TRMR", "3-5 years")] == (0.5, 1.0, 1.5)
    assert baselines["progression"]["NP2TRMR"] == 0.1


def test_no_baseline_files_means_empty_baselines(app):
    assert load_baselines() == NO_BASELINES            # PPMI_DATA_DIR is off in tests


def _rising_tremor(patient, tremor):
    """Tremor climbing 1 -> 3 over the last two weeks (all within the 30-day window)."""
    now = datetime.utcnow()
    for days_ago, score in [(14, 1), (11, 2), (8, 2), (5, 3), (2, 3), (0, 3)]:
        make_symptom(patient, tremor, score, when=now - timedelta(days=days_ago, hours=1))


def test_insight_compares_with_the_fake_baseline(app, tmp_path, patient, tremor):
    _write_fake_baselines(tmp_path)
    app.config["PPMI_DATA_DIR"] = str(tmp_path)
    try:
        _rising_tremor(patient, tremor)
        texts = build_insights(patient, datetime.utcnow(), load_baselines())
    finally:
        app.config["PPMI_DATA_DIR"] = None
    tremor_text = next(t for t in texts if t.startswith("Tremor:"))
    assert "getting worse" in tremor_text
    assert "above the typical range for people 3-5 years after diagnosis in PPMI (0.5–1.5)" in tremor_text
    assert "Much faster than typical for Parkinson's (PPMI: about +0.10 a year)" in tremor_text


def test_without_diagnosis_year_the_typical_comparison_is_skipped(app, tmp_path, patient, tremor):
    patient.diagnosis_year = None
    db.session.commit()
    _write_fake_baselines(tmp_path)
    app.config["PPMI_DATA_DIR"] = str(tmp_path)
    try:
        _rising_tremor(patient, tremor)
        texts = build_insights(patient, datetime.utcnow(), load_baselines())
    finally:
        app.config["PPMI_DATA_DIR"] = None
    tremor_text = next(t for t in texts if t.startswith("Tremor:"))
    assert "getting worse" in tremor_text                  # feature 1 still works
    assert "typical range" not in tremor_text              # feature 2 skipped


# ---------------------------------------------------------------------------
# Feature 3: wearing-off
# ---------------------------------------------------------------------------

DOSES = [T0 + timedelta(days=k) for k in range(10)]        # one dose a day at T0's time


def _around_doses(pairs):
    """[(day, hours after that day's dose, score)] -> entries."""
    return [(DOSES[k] + timedelta(hours=h), s) for k, h, s in pairs]


def test_worse_later_after_a_dose_is_flagged():
    entries = _around_doses([(k, 1, 1) for k in range(4)] + [(k, 4, 3) for k in range(4)])
    assert wearing_off(entries, DOSES) == {"soon": 1.0, "later": 3.0, "n_soon": 4, "n_later": 4}


def test_wearing_off_not_flagged_without_a_pattern():
    same = _around_doses([(k, 1, 2) for k in range(4)] + [(k, 4, 2) for k in range(4)])
    better_later = _around_doses([(k, 1, 3) for k in range(4)] + [(k, 4, 1) for k in range(4)])
    too_few = _around_doses([(k, 1, 1) for k in range(2)] + [(k, 4, 3) for k in range(4)])
    no_recent_dose = _around_doses([(k, 1, 1) for k in range(4)] + [(k, 14, 3) for k in range(4)])
    for entries in (same, better_later, too_few, no_recent_dose):
        assert wearing_off(entries, DOSES) is None


# ---------------------------------------------------------------------------
# Running the component
# ---------------------------------------------------------------------------

def test_analyze_replaces_old_insights_instead_of_adding(patient, tremor):
    _rising_tremor(patient, tremor)
    first = analyze_patient(patient, baselines=NO_BASELINES)
    second = analyze_patient(patient, baselines=NO_BASELINES)
    assert first == second > 0
    assert Insight.query.filter_by(patient_id=patient.patient_id).count() == second


def test_patient_with_no_entries_gets_no_insights(patient):
    assert analyze_patient(patient, baselines=NO_BASELINES) == 0


# ---------------------------------------------------------------------------
# Feature 4: missed doses vs symptoms
# a symptom is flagged if it is >= 0.75 higher in the 24 h after a missed dose than at other
# times the same week, with >= 3 entries in each group from >= 2 different missed doses.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Feature 4: after_missed_doses() on made-up data
# ---------------------------------------------------------------------------

def test_clear_pattern_is_flagged():
    misses = [at(0), at(2), at(4)]
    entries = ([(at(d, 4), 3) for d in (0, 2, 4)]          # 4 h after each miss: high
               + [(at(d, 4), 1) for d in (1, 3, 5)])        # in between (> 24 h after): low
    result = after_missed_doses(entries, misses)
    assert result == {"after": 3.0, "normal": 1.0, "n_after": 3, "n_normal": 3, "misses": 3}


def test_no_difference_is_not_flagged():
    misses = [at(0), at(2), at(4)]
    entries = [(at(d, 4), 2) for d in range(6)]
    assert after_missed_doses(entries, misses) is None


def test_one_missed_dose_is_not_enough():
    misses = [at(0)]
    entries = ([(at(0, h), 3) for h in (2, 4, 6)]           # 3 high entries, but all after ONE miss
               + [(at(d, 4), 1) for d in (2, 3)] + [(at(-2), 1)])
    assert after_missed_doses(entries, misses) is None


def test_too_few_entries_in_a_group_is_not_flagged():
    misses = [at(0), at(2)]
    entries = [(at(0, 4), 3), (at(2, 4), 3)] + [(at(d, 4), 1) for d in (1, 3, 4)]  # only 2 "after"
    assert after_missed_doses(entries, misses) is None


def test_entries_more_than_24h_after_count_as_normal():
    misses = [at(0), at(3), at(6)]
    entries = ([(at(d, 4), 3) for d in (0, 3, 6)]          # 4 h after: "after a miss"
               + [(at(d, 25), 1) for d in (0, 3, 6)])       # 25 h after: outside the window -> "normal"
    result = after_missed_doses(entries, misses)
    assert (result["n_after"], result["n_normal"]) == (3, 3)
    assert (result["after"], result["normal"]) == (3.0, 1.0)


def test_entries_far_from_any_miss_are_ignored():
    """Only entries from the same week as a miss are used as 'normal' (avoids trend effects)."""
    misses = [at(0), at(2), at(4)]
    entries = ([(at(d, 4), 3) for d in (0, 2, 4)]
               + [(at(d, 4), 1) for d in (1, 3, 5)]
               + [(at(d), 4) for d in (20, 21, 22)])         # weeks later: not part of the comparison
    assert after_missed_doses(entries, misses)["n_normal"] == 3


# ---------------------------------------------------------------------------
# Which doses count as missed (reads the current status from the database)
# ---------------------------------------------------------------------------

def test_skipped_and_corrected_doses_do_not_count_as_missed(patient, medication):
    missed = make_log(medication, "missed", days_ago=3)
    make_log(medication, "skipped", days_ago=2)
    corrected = make_log(medication, "missed", days_ago=1)
    corrected.original_status, corrected.status = "missed", "taken"   # patient fixed a wrong tap
    db.session.commit()
    since = datetime.utcnow() - timedelta(days=30)
    assert missed_dose_times(patient, since) == [missed.scheduled_time]


def test_insight_text_after_missed_doses(patient, medication, tremor):
    """End to end: 3 missed doses with higher Tremor afterwards -> one insight with the averages."""
    for days_ago in (5, 3, 1):
        miss = make_log(medication, "missed", days_ago=days_ago, hour=13)
        make_symptom(patient, tremor, 3, when=miss.scheduled_time + timedelta(hours=4))   # after the miss
        make_symptom(patient, tremor, 1, when=miss.scheduled_time + timedelta(days=1, hours=6))  # next day
    texts = build_insights(patient, datetime.utcnow(), NO_BASELINES)
    flagged = [t for t in texts if "after a missed dose" in t]
    assert len(flagged) == 1
    assert flagged[0].startswith("Tremor:")
    assert "averaging 3.0 vs 1.0" in flagged[0]
    assert "after 3 missed doses" in flagged[0]


# ---------------------------------------------------------------------------
# "Update insights" button (patient dashboard + HCP patient page)
# ---------------------------------------------------------------------------

def _tremor_rising_lately(patient, tremor):
    for days_ago, severity in [(12, 0), (10, 0), (8, 1), (6, 1), (4, 2), (2, 2)]:
        make_symptom(patient, tremor, severity, when=datetime.utcnow() - timedelta(days=days_ago))


def test_patient_update_insights_replaces_old_ones(client, patient, tremor):
    from conftest import login
    db.session.add(Insight(patient_id=patient.patient_id, content="old insight"))
    db.session.commit()
    _tremor_rising_lately(patient, tremor)
    login(client, "patient", patient.email)

    response = client.post("/patient/insights/update", follow_redirects=True)

    assert b"Insights updated" in response.data
    texts = [i.content for i in Insight.query.filter_by(patient_id=patient.patient_id)]
    assert "old insight" not in texts
    assert any("Tremor" in t for t in texts)


def test_update_insights_with_no_data_says_keep_logging(client, patient):
    from conftest import login
    login(client, "patient", patient.email)
    response = client.post("/patient/insights/update", follow_redirects=True)
    assert b"enough logged yet" in response.data


def test_hcp_can_update_only_assigned_patients_insights(client, hcp, patient, other_patient, tremor):
    from conftest import login
    _tremor_rising_lately(patient, tremor)
    login(client, "hcp", hcp.email)
    page = client.get(f"/hcp/patient/{patient.patient_id}").get_data(as_text=True)
    assert "Update insights" in page

    response = client.post(f"/hcp/patient/{patient.patient_id}/insights/update", follow_redirects=True)
    assert b"Insights updated" in response.data
    assert Insight.query.filter_by(patient_id=patient.patient_id).count() > 0
    assert client.post(f"/hcp/patient/{other_patient.patient_id}/insights/update").status_code == 404


def test_caregiver_view_has_no_update_button(client, caregiver, patient):
    from conftest import login
    login(client, "caregiver", caregiver.email)
    page = client.get(f"/caregiver/patient/{patient.patient_id}/dashboard").get_data(as_text=True)
    assert "Update insights" not in page
