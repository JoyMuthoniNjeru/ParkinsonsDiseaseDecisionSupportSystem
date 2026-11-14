"""
Corrections (supervisor demo feedback, 4 Nov): a dose tapped wrongly can be changed within 3 days,
a symptom entry can be edited / deleted on the same day, and the alerts follow the correction.
"""
from datetime import datetime, timedelta

from conftest import login, make_log, make_symptom
from app.extensions import db
from app.models import Alert, MedicationLog, SymptomRecord
from app.rule_engine import (
    evaluate_medication_log, evaluate_symptom_entry, on_dose_corrected, PATTERN_ALERT_PREFIX,
)


def alert_texts(patient):
    return sorted(a.rule_triggered for a in Alert.query.filter_by(patient_id=patient.patient_id))


def count_patterns(patient):
    return sum(t.startswith(PATTERN_ALERT_PREFIX) for t in alert_texts(patient))


def change(client, log, **form):
    return client.post(f"/patient/doses/{log.log_id}/change", data=form, follow_redirects=True)


# ---------------------------------------------------------------------------
# Rule engine: on_dose_corrected
# ---------------------------------------------------------------------------

def _three_missed(medication):
    logs = [make_log(medication, "missed", days_ago=d) for d in (3, 2, 1)]
    for log in logs:
        evaluate_medication_log(log)
    return logs


def _correct(log, new_status):
    old = log.status
    log.status = new_status
    db.session.commit()
    return on_dose_corrected(log, old)


def test_missed_to_taken_removes_dose_alert_and_pattern(patient, medication):
    logs = _three_missed(medication)
    assert count_patterns(patient) == 1
    _correct(logs[1], "taken")                            # only 2 missed left
    assert count_patterns(patient) == 0
    assert len(alert_texts(patient)) == 2                 # the other two per-dose alerts stay


def test_taken_to_missed_raises_alerts_like_a_normal_tap(patient, medication):
    logs = _three_missed(medication)
    _correct(logs[1], "taken")
    _correct(logs[1], "missed")                           # the MIDDLE dose: pattern must come back
    assert count_patterns(patient) == 1
    assert len(alert_texts(patient)) == 4


def test_taken_to_late_changes_no_alerts(patient, medication):
    log = make_log(medication, "taken")
    assert _correct(log, "late") == (0, [])
    assert alert_texts(patient) == []


def test_pattern_survives_if_three_misses_remain(patient, medication):
    logs = [make_log(medication, "missed", days_ago=d) for d in (4, 3, 2, 1)]
    for log in logs:
        evaluate_medication_log(log)
    _correct(logs[0], "taken")                            # 3 misses still inside 7 days
    assert count_patterns(patient) == 1


# ---------------------------------------------------------------------------
# Dose correction through the app (patient's Change page)
# ---------------------------------------------------------------------------

def test_patient_corrects_missed_to_taken(client, patient, medication):
    log = make_log(medication, "missed", days_ago=1)
    evaluate_medication_log(log)
    login(client, "patient", patient.email)

    response = change(client, log, status="taken", taken_time="13:10")

    assert b"1 alert removed" in response.data
    db.session.refresh(log)
    assert (log.status, log.original_status) == ("taken", "missed")
    assert log.corrected_at is not None
    assert alert_texts(patient) == []


def test_late_is_recalculated_from_the_new_time(client, patient, medication):
    log = make_log(medication, "missed", days_ago=1, hour=13)
    login(client, "patient", patient.email)
    change(client, log, status="taken", taken_time="13:45")   # > 30 min after 13:00
    db.session.refresh(log)
    assert log.status == "late"


def test_original_status_is_kept_from_the_first_correction(client, patient, medication):
    log = make_log(medication, "missed", days_ago=1)
    login(client, "patient", patient.email)
    change(client, log, status="taken", taken_time="13:05")
    change(client, log, status="skipped")
    db.session.refresh(log)
    assert (log.status, log.original_status) == ("skipped", "missed")


def test_correction_outside_the_3_day_window_is_rejected(client, patient, medication):
    log = make_log(medication, "missed", days_ago=5)
    login(client, "patient", patient.email)
    response = change(client, log, status="taken", taken_time="13:05")
    assert b"Only doses from today and the 2 days before" in response.data
    db.session.refresh(log)
    assert log.status == "missed" and log.corrected_at is None


def test_taken_time_in_the_future_is_rejected(client, patient, medication):
    log = make_log(medication, "missed", days_ago=0, hour=8)
    future = (datetime.utcnow() + timedelta(hours=3, minutes=30)).strftime("%H:%M")  # local + 30 min
    login(client, "patient", patient.email)
    response = change(client, log, status="taken", taken_time=future)
    if future > "00:30":                                  # (skip the check right around midnight)
        assert b"can&#39;t be in the future" in response.data
        db.session.refresh(log)
        assert log.status == "missed"


def test_patient_cannot_change_another_patients_dose(client, patient, other_patient, medication):
    log = make_log(medication, "missed", days_ago=1)
    login(client, "patient", other_patient.email)
    assert client.get(f"/patient/doses/{log.log_id}/change").status_code == 404
    assert client.post(f"/patient/doses/{log.log_id}/change", data={"status": "taken"}).status_code == 404


def test_extra_dose_can_be_deleted_but_scheduled_dose_cannot(client, patient, medication):
    login(client, "patient", patient.email)
    client.post("/patient/doses/extra", data={"patient_medication_id": medication.patient_medication_id})
    extra = MedicationLog.query.filter_by(schedule_id=None).one()
    client.post(f"/patient/doses/{extra.log_id}/delete")
    assert MedicationLog.query.filter_by(schedule_id=None).count() == 0

    scheduled = make_log(medication, "taken", days_ago=1)
    response = client.post(f"/patient/doses/{scheduled.log_id}/delete", follow_redirects=True)
    assert b"Scheduled doses can&#39;t be deleted" in response.data
    assert db.session.get(MedicationLog, scheduled.log_id) is not None


# ---------------------------------------------------------------------------
# Symptom edit / delete (same local day only)
# ---------------------------------------------------------------------------

def _edit(client, record, severity):
    return client.post(f"/patient/symptoms/{record.record_id}/edit",
                       data={"severity": severity}, follow_redirects=True)


def test_edit_2_to_3_creates_alert_and_3_to_2_removes_it(client, patient, tremor):
    record = make_symptom(patient, tremor, 2)
    evaluate_symptom_entry(record)
    login(client, "patient", patient.email)

    _edit(client, record, 3)
    assert alert_texts(patient) == ["Moderate symptom: Tremor 3/4"]
    _edit(client, record, 2)
    assert alert_texts(patient) == []

    db.session.refresh(record)
    assert (record.severity, record.original_severity) == (2, 2)   # original = before the FIRST edit
    assert record.edited_at is not None


def test_edit_3_to_4_updates_the_alert_text(client, patient, tremor):
    record = make_symptom(patient, tremor, 3)
    evaluate_symptom_entry(record)
    login(client, "patient", patient.email)
    _edit(client, record, 4)
    assert alert_texts(patient) == ["Severe symptom: Tremor 4/4"]


def test_deleting_an_entry_deletes_its_alert(client, patient, tremor):
    record = make_symptom(patient, tremor, 3)
    evaluate_symptom_entry(record)
    login(client, "patient", patient.email)
    client.post(f"/patient/symptoms/{record.record_id}/delete")
    assert SymptomRecord.query.count() == 0
    assert alert_texts(patient) == []


def test_yesterdays_entry_cannot_be_edited(client, patient, tremor):
    record = make_symptom(patient, tremor, 2, when=datetime.utcnow() - timedelta(days=1, hours=1))
    login(client, "patient", patient.email)
    response = _edit(client, record, 4)
    assert b"only be changed on the day they were logged" in response.data
    db.session.refresh(record)
    assert record.severity == 2


def test_patient_cannot_edit_another_patients_entry(client, patient, other_patient, tremor):
    record = make_symptom(patient, tremor, 2)
    login(client, "patient", other_patient.email)
    assert client.post(f"/patient/symptoms/{record.record_id}/edit", data={"severity": 4}).status_code == 404


def test_invalid_score_on_edit_is_rejected(client, patient, tremor):
    record = make_symptom(patient, tremor, 2)
    login(client, "patient", patient.email)
    _edit(client, record, 7)
    db.session.refresh(record)
    assert record.severity == 2 and record.edited_at is None


# ---------------------------------------------------------------------------
# Skipped in corrections
# ---------------------------------------------------------------------------

def test_missed_to_skipped_swaps_the_alert(patient, medication):
    log = make_log(medication, "missed")
    evaluate_medication_log(log)
    _correct(log, "skipped")
    assert alert_texts(patient) == ["Skipped dose: Pramipexole 0.25 mg at 13:00"]


def test_skipped_to_taken_removes_the_alert(patient, medication):
    log = make_log(medication, "skipped")
    evaluate_medication_log(log)
    _correct(log, "taken")
    assert alert_texts(patient) == []


def test_correcting_a_miss_to_skipped_breaks_the_pattern(patient, medication):
    logs = _three_missed(medication)
    _correct(logs[0], "skipped")
    assert count_patterns(patient) == 0                   # only 2 missed left
    assert len(alert_texts(patient)) == 3                 # 2 missed + 1 skipped


# ---------------------------------------------------------------------------
# Caregiver / HCP pages: medications + 7-day dose history (incl. the "Corrected" badge)
# ---------------------------------------------------------------------------

def test_hcp_sees_medications_and_dose_history_with_corrected_badge(client, hcp, patient, medication):
    make_log(medication, "missed", days_ago=5)            # outside the patient's 3-day window, inside 7
    taken = make_log(medication, "taken", days_ago=1, hour=8)
    corrected = make_log(medication, "missed", days_ago=2)
    corrected.original_status, corrected.status = "taken", "missed"
    corrected.corrected_at = datetime.utcnow()
    db.session.commit()

    login(client, "hcp", hcp.email)
    page = client.get(f"/hcp/patient/{patient.patient_id}").get_data(as_text=True)

    assert "Medications" in page and "Pramipexole 0.25 mg" in page and "13:00" in page
    assert "Dose history" in page
    assert "1 taken" in page and "2 missed" in page and "0 skipped" in page
    assert "Corrected" in page and "originally taken" in page
    assert "/patient/doses/" not in page                  # dose history is read-only: no dose Change links


def test_dose_history_leaves_out_doses_older_than_7_days(client, caregiver, patient, medication):
    make_log(medication, "missed", days_ago=9)
    login(client, "caregiver", caregiver.email)
    page = client.get(f"/caregiver/patient/{patient.patient_id}/dashboard").get_data(as_text=True)
    assert "No doses logged in the last 7 days" in page


def test_ended_medication_is_not_listed_as_current(client, hcp, patient, medication):
    from datetime import date
    medication.end_date = date.today() - timedelta(days=1)
    db.session.commit()
    login(client, "hcp", hcp.email)
    page = client.get(f"/hcp/patient/{patient.patient_id}").get_data(as_text=True)
    assert "No current medications" in page
