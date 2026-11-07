"""
Rule-Based Engine: the three alert rules (CLAUDE.md "Scope decisions").
  1. severity >= 3 -> alert
  2. every missed dose -> alert
  3. >= 3 missed doses in 7 days -> one pattern alert (no duplicates)
Plus Undo removing the alerts a dose raised.
"""
from datetime import datetime, timedelta

from conftest import login, make_log, make_symptom, UTC_OFFSET
from app.extensions import db
from app.models import Alert, MedicationSchedule
from app.rule_engine import evaluate_symptom_entry, evaluate_medication_log, PATTERN_ALERT_PREFIX


def alerts(patient):
    return Alert.query.filter_by(patient_id=patient.patient_id).order_by(Alert.timestamp).all()


def patterns(patient):
    return [a for a in alerts(patient) if a.rule_triggered.startswith(PATTERN_ALERT_PREFIX)]


# ---------------------------------------------------------------------------
# Rule 1: symptom severity
# ---------------------------------------------------------------------------

def test_severity_below_3_raises_no_alert(patient, tremor):
    record = make_symptom(patient, tremor, 2)
    assert evaluate_symptom_entry(record) is None
    assert alerts(patient) == []


def test_severity_3_raises_alert_with_label(patient, tremor):
    record = make_symptom(patient, tremor, 3)
    alert = evaluate_symptom_entry(record)
    assert alert.rule_triggered == "Moderate symptom: Tremor 3/4"
    assert alert.symptom_record_id == record.record_id   # linked, so edits can find it


def test_severity_4_says_severe(patient, tremor):
    alert = evaluate_symptom_entry(make_symptom(patient, tremor, 4))
    assert alert.rule_triggered == "Severe symptom: Tremor 4/4"


# ---------------------------------------------------------------------------
# Rule 2: every missed dose
# ---------------------------------------------------------------------------

def test_taken_dose_raises_no_alert(patient, medication):
    assert evaluate_medication_log(make_log(medication, "taken")) == []
    assert alerts(patient) == []


def test_missed_dose_raises_alert_with_dose_and_time(patient, medication):
    log = make_log(medication, "missed", hour=13)
    raised = evaluate_medication_log(log)
    assert [a.rule_triggered for a in raised] == ["Missed dose: Pramipexole 0.25 mg at 13:00"]
    assert raised[0].medication_log_id == log.log_id


# ---------------------------------------------------------------------------
# Rule 3: pattern of missed doses
# ---------------------------------------------------------------------------

def test_two_missed_in_7_days_is_not_a_pattern(patient, medication):
    for days_ago in (3, 2):
        evaluate_medication_log(make_log(medication, "missed", days_ago=days_ago))
    assert patterns(patient) == []
    assert len(alerts(patient)) == 2                      # just the per-dose alerts


def test_three_missed_in_7_days_raises_one_pattern_alert(patient, medication):
    for days_ago in (3, 2, 1):
        evaluate_medication_log(make_log(medication, "missed", days_ago=days_ago))
    assert [a.rule_triggered for a in patterns(patient)] == [
        f"{PATTERN_ALERT_PREFIX}: 3 missed doses in the last 7 days"]


def test_fourth_missed_dose_does_not_duplicate_the_pattern(patient, medication):
    for days_ago in (4, 3, 2, 1):
        evaluate_medication_log(make_log(medication, "missed", days_ago=days_ago))
    assert len(patterns(patient)) == 1
    assert len(alerts(patient)) == 5                      # 4 per-dose + 1 pattern


def test_missed_dose_older_than_7_days_does_not_count(patient, medication):
    for days_ago in (9, 2, 1):                            # 9 days ago is outside the window
        evaluate_medication_log(make_log(medication, "missed", days_ago=days_ago))
    assert patterns(patient) == []


# ---------------------------------------------------------------------------
# Undo (patient taps Missed, then Undo within 10 minutes)
# ---------------------------------------------------------------------------

def _due_schedule(medication):
    """A schedule that is 'Due now' whatever time the tests run (10 min from now, local)."""
    local_now = datetime.utcnow() + UTC_OFFSET
    schedule = MedicationSchedule(patient_medication_id=medication.patient_medication_id,
                                  dose_time=(local_now + timedelta(minutes=10)).time().replace(second=0, microsecond=0))
    db.session.add(schedule)
    db.session.commit()
    return schedule


def test_undo_removes_the_dose_and_its_alerts(client, patient, medication):
    for days_ago in (2, 1):                               # 2 earlier misses: the 3rd makes a pattern
        evaluate_medication_log(make_log(medication, "missed", days_ago=days_ago))
    schedule = _due_schedule(medication)
    login(client, "patient", patient.email)

    client.post("/patient/doses/log", data={"schedule_id": schedule.schedule_id, "status": "missed"})
    assert len(alerts(patient)) == 4                      # 3 per-dose + the pattern
    client.get("/patient/dashboard")                      # shows the Undo bar
    response = client.post("/patient/doses/undo", follow_redirects=True)

    assert b"Undone" in response.data
    assert len(alerts(patient)) == 2                      # back to the 2 earlier per-dose alerts
    assert patterns(patient) == []


# ---------------------------------------------------------------------------
# Skipped doses (decided 7 Nov): own alert, but not part of the missed-dose pattern
# ---------------------------------------------------------------------------

def test_skipped_dose_raises_its_own_alert(patient, medication):
    log = make_log(medication, "skipped", hour=8)
    raised = evaluate_medication_log(log)
    assert [a.rule_triggered for a in raised] == ["Skipped dose: Pramipexole 0.25 mg at 08:00"]
    assert raised[0].medication_log_id == log.log_id


def test_skipped_doses_do_not_count_towards_the_pattern(patient, medication):
    for days_ago, status in [(3, "missed"), (2, "skipped"), (1, "missed")]:
        evaluate_medication_log(make_log(medication, status, days_ago=days_ago))
    assert patterns(patient) == []                        # only 2 MISSED in the week
    assert len(alerts(patient)) == 3                      # but all 3 doses have their own alert
