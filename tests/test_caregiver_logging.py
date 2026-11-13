"""
Step 2 of the 80% check: a caregiver logs symptoms and Taken / Missed doses for a linked patient.
Approved link only; same Rule Engine; saved with the caregiver's id and shown as "Logged by ...";
the caregiver can Undo only a dose they logged themselves.
"""
from datetime import datetime, timedelta

from conftest import login, make_log, make_symptom, _add, UTC_OFFSET
from app.extensions import db
from app.models import Alert, Caregiver, CaregiverLink, MedicationLog, MedicationSchedule, SymptomRecord


def _due_now(medication):
    """A schedule that is 'Due now' whatever time the tests run (10 min from now, local)."""
    local_now = datetime.utcnow() + UTC_OFFSET
    s = MedicationSchedule(patient_medication_id=medication.patient_medication_id,
                           dose_time=(local_now + timedelta(minutes=10)).time().replace(second=0, microsecond=0))
    db.session.add(s)
    db.session.commit()
    return s


def test_caregiver_logs_symptoms_for_a_linked_patient(client, caregiver, patient, tremor):
    login(client, "caregiver", caregiver.email)
    page = client.get(f"/caregiver/patient/{patient.patient_id}/symptoms").get_data(as_text=True)
    assert "Log symptoms for Pat" in page

    response = client.post(f"/caregiver/patient/{patient.patient_id}/symptoms",
                           data={f"severity_{tremor.symptom_type_id}": 3}, follow_redirects=True)

    assert b"Saved 1 symptom" in response.data
    record = SymptomRecord.query.one()
    assert (record.patient_id, record.recorded_by_caregiver_id) == (patient.patient_id, caregiver.caregiver_id)
    assert [a.rule_triggered for a in Alert.query.all()] == ["Moderate symptom: Tremor 3/4"]   # same rules


def test_caregiver_logs_a_due_dose(client, caregiver, patient, medication):
    schedule = _due_now(medication)
    login(client, "caregiver", caregiver.email)
    page = client.get(f"/caregiver/patient/{patient.patient_id}/dashboard").get_data(as_text=True)
    assert 'value="missed"' in page                       # Taken / Missed buttons on the due dose

    client.post(f"/caregiver/patient/{patient.patient_id}/doses/log",
                data={"schedule_id": schedule.schedule_id, "status": "missed"})

    log = MedicationLog.query.one()
    assert (log.status, log.logged_by_caregiver_id) == ("missed", caregiver.caregiver_id)
    assert Alert.query.filter(Alert.rule_triggered.startswith("Missed dose")).count() == 1


def test_caregiver_with_pending_link_cannot_log(client, patient, medication, tremor):
    carer = _add(Caregiver(name="Waiting Carer", email="waiting@test.local"))
    db.session.add(CaregiverLink(patient_id=patient.patient_id, caregiver_id=carer.caregiver_id, status="pending"))
    db.session.commit()
    schedule = _due_now(medication)
    login(client, "caregiver", carer.email)

    assert client.post(f"/caregiver/patient/{patient.patient_id}/symptoms",
                       data={f"severity_{tremor.symptom_type_id}": 2}).status_code == 404
    assert client.post(f"/caregiver/patient/{patient.patient_id}/doses/log",
                       data={"schedule_id": schedule.schedule_id, "status": "taken"}).status_code == 404
    assert SymptomRecord.query.count() == 0 and MedicationLog.query.count() == 0


def test_caregiver_cannot_log_for_an_unlinked_patient(client, caregiver, other_patient, tremor):
    login(client, "caregiver", caregiver.email)
    assert client.get(f"/caregiver/patient/{other_patient.patient_id}/symptoms").status_code == 404


def test_caregiver_bad_score_saves_nothing(client, caregiver, patient, tremor):
    login(client, "caregiver", caregiver.email)
    client.post(f"/caregiver/patient/{patient.patient_id}/symptoms", data={f"severity_{tremor.symptom_type_id}": 9})
    assert SymptomRecord.query.count() == 0


def test_caregiver_can_undo_their_own_dose(client, caregiver, patient, medication):
    schedule = _due_now(medication)
    login(client, "caregiver", caregiver.email)
    client.post(f"/caregiver/patient/{patient.patient_id}/doses/log",
                data={"schedule_id": schedule.schedule_id, "status": "missed"})
    assert b"Undo" in client.get(f"/caregiver/patient/{patient.patient_id}/dashboard").data

    response = client.post("/caregiver/doses/undo", follow_redirects=True)

    assert b"Undone" in response.data
    assert MedicationLog.query.count() == 0 and Alert.query.count() == 0


def test_caregiver_cannot_undo_the_patients_own_dose(client, caregiver, patient, medication):
    patients_log = make_log(medication, "missed", days_ago=0)
    patients_log.timestamp = datetime.utcnow()             # within the 10 min window
    db.session.commit()
    login(client, "caregiver", caregiver.email)
    with client.session_transaction() as s:                # even with a tampered session
        s["undo_dose"] = {"log_id": patients_log.log_id, "message": "x"}
    response = client.post("/caregiver/doses/undo", follow_redirects=True)
    assert b"can no longer be undone" in response.data
    assert db.session.get(MedicationLog, patients_log.log_id) is not None


def test_patient_cannot_undo_a_caregivers_dose(client, patient, caregiver, medication):
    log = make_log(medication, "missed", days_ago=0)
    log.timestamp, log.logged_by_caregiver_id = datetime.utcnow(), caregiver.caregiver_id
    db.session.commit()
    login(client, "patient", patient.email)
    with client.session_transaction() as s:
        s["undo_dose"] = {"log_id": log.log_id, "message": "x"}
    client.post("/patient/doses/undo")
    assert db.session.get(MedicationLog, log.log_id) is not None   # patient uses Change instead


def test_logged_by_badge_shows_for_patient_and_hcp(client, caregiver, patient, hcp, tremor):
    record = make_symptom(patient, tremor, 2)
    record.recorded_by_caregiver_id = caregiver.caregiver_id
    db.session.commit()

    login(client, "patient", patient.email)
    assert "Logged by Carer" in client.get("/patient/trends").get_data(as_text=True)
    hcp_client = client.application.test_client()
    login(hcp_client, "hcp", hcp.email)
    assert "Logged by Carer" in hcp_client.get(f"/hcp/patient/{patient.patient_id}").get_data(as_text=True)


def test_hcp_page_has_no_dose_buttons(client, hcp, patient, medication):
    _due_now(medication)
    login(client, "hcp", hcp.email)
    page = client.get(f"/hcp/patient/{patient.patient_id}").get_data(as_text=True)
    assert 'value="taken"' not in page and "Due now" in page          # read-only for the HCP


def test_patient_cannot_edit_or_delete_an_entry_the_caregiver_logged(client, caregiver, patient, tremor):
    own = make_symptom(patient, tremor, 1)
    carers = make_symptom(patient, tremor, 2)
    carers.recorded_by_caregiver_id = caregiver.caregiver_id
    db.session.commit()
    login(client, "patient", patient.email)

    page = client.get("/patient/trends").get_data(as_text=True)
    assert f"/patient/symptoms/{own.record_id}/edit" in page          # own entry: Edit link
    assert f"/patient/symptoms/{carers.record_id}/edit" not in page   # caregiver's: no Edit link

    edit = client.post(f"/patient/symptoms/{carers.record_id}/edit", data={"severity": 4}, follow_redirects=True)
    assert b"logged by your caregiver" in edit.data
    client.post(f"/patient/symptoms/{carers.record_id}/delete")
    db.session.refresh(carers)
    assert carers.severity == 2 and carers.edited_at is None          # unchanged, not deleted
