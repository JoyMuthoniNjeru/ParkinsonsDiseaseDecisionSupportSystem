"""
ML Component (app/ml_component.py). Never reads PPMI: baselines are passed in empty here.

Feature 4 (missed doses vs symptoms): a symptom is flagged if it is >= 0.75 higher in the
24 h after a missed dose than at other times the same week, with >= 3 entries in each group
from >= 2 different missed doses.
"""
from datetime import datetime, timedelta

from conftest import make_log, make_symptom
from app.extensions import db
from app.ml_component import after_missed_doses, missed_dose_times, build_insights

NO_BASELINES = {"progression": {}, "severity": {}}
T0 = datetime(2026, 10, 1, 10, 0)  # a missed dose at T0, T0 + 2 days, ...


def at(days, hours=0):
    return T0 + timedelta(days=days, hours=hours)


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
