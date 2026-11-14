"""
Step 5 of the 80% check: only the patient's assigned HCP adds, changes and stops medications;
the patient's My medications is read-only.
Change: instructions / end date in place; a new dose or times = old prescription stops, new one
starts today (old dose logs stay with the old schedule).
"""
from datetime import timedelta

from conftest import login, make_log, _add, local_today
from app.extensions import db
from app.models import HealthcareProfessional, MedicationLog, MedicationReference, PatientMedication


def _form(med_id, **overrides):
    form = {"medication_id": med_id, "dose_amount": "0.25", "dose_unit": "mg", "form": "tablet",
            "quantity_per_dose": "1", "times_per_day": "3", "dose_times": ["08:00", "13:00", "18:00"]}
    form.update(overrides)
    return form


def test_hcp_prescribes_for_an_assigned_patient(client, hcp, patient):
    med = MedicationReference(name="Ropinirole")
    db.session.add(med)
    db.session.commit()
    login(client, "hcp", hcp.email)
    assert client.get(f"/hcp/patient/{patient.patient_id}/medications/add").status_code == 200

    response = client.post(f"/hcp/patient/{patient.patient_id}/medications/add",
                           data=_form(med.medication_id), follow_redirects=True)

    assert b"Prescribed Ropinirole 0.25 mg" in response.data
    pm = PatientMedication.query.one()
    assert (pm.patient_id, pm.prescribed_by_hcp_id, len(pm.schedules)) == (patient.patient_id, hcp.hcp_id, 3)


def test_another_hcp_gets_404(client, patient, medication):
    other = _add(HealthcareProfessional(name="Dr Other", email="other.hcp@test.local",
                                        institution="X", credential_status="approved"))
    login(client, "hcp", other.email)
    pm_id = medication.patient_medication_id
    assert client.get(f"/hcp/patient/{patient.patient_id}/medications/add").status_code == 404
    assert client.get(f"/hcp/patient/{patient.patient_id}/medications/{pm_id}/change").status_code == 404
    assert client.post(f"/hcp/patient/{patient.patient_id}/medications/{pm_id}/stop").status_code == 404


def test_medication_of_another_patient_gives_404(client, hcp, patient, other_patient, medication):
    other_patient.hcp_id = hcp.hcp_id                    # both assigned, but the pm is patient's
    db.session.commit()
    login(client, "hcp", hcp.email)
    url = f"/hcp/patient/{other_patient.patient_id}/medications/{medication.patient_medication_id}/stop"
    assert client.post(url).status_code == 404


def test_changing_instructions_edits_in_place(client, hcp, patient, medication):
    login(client, "hcp", hcp.email)
    page = client.get(f"/hcp/patient/{patient.patient_id}/medications/{medication.patient_medication_id}/change")
    assert b'value="0.25"' in page.data                   # form pre-filled with the current dose

    client.post(f"/hcp/patient/{patient.patient_id}/medications/{medication.patient_medication_id}/change",
                data=_form(medication.medication_id, instructions="Take with food"))

    assert PatientMedication.query.count() == 1
    db.session.refresh(medication)
    assert medication.instructions == "Take with food" and medication.end_date is None


def test_changing_the_times_stops_the_old_and_starts_a_new_prescription(client, hcp, patient, medication):
    old_log = make_log(medication, "taken", days_ago=1)
    login(client, "hcp", hcp.email)

    response = client.post(
        f"/hcp/patient/{patient.patient_id}/medications/{medication.patient_medication_id}/change",
        data=_form(medication.medication_id, times_per_day="2", dose_times=["09:00", "21:00"]),
        follow_redirects=True)

    assert b"New prescription from today" in response.data
    db.session.refresh(medication)
    new = PatientMedication.query.filter(PatientMedication.patient_medication_id != medication.patient_medication_id).one()
    assert medication.end_date == local_today() - timedelta(days=1)      # old one stopped
    assert new.started_date == local_today() and new.prescribed_by_hcp_id == hcp.hcp_id
    assert [s.dose_time.strftime("%H:%M") for s in new.schedules] == ["09:00", "21:00"]
    assert db.session.get(MedicationLog, old_log.log_id).patient_medication_id == medication.patient_medication_id


def test_the_drug_itself_cant_be_swapped_on_change(client, hcp, patient, medication):
    other = MedicationReference(name="Rasagiline")
    db.session.add(other)
    db.session.commit()
    login(client, "hcp", hcp.email)
    client.post(f"/hcp/patient/{patient.patient_id}/medications/{medication.patient_medication_id}/change",
                data=_form(other.medication_id, instructions="x"))
    assert {pm.medication_id for pm in PatientMedication.query} == {medication.medication_id}


def test_stopping_removes_it_from_todays_doses_but_keeps_history(client, hcp, patient, medication):
    log = make_log(medication, "missed", days_ago=1)
    login(client, "hcp", hcp.email)
    client.post(f"/hcp/patient/{patient.patient_id}/medications/{medication.patient_medication_id}/stop")

    db.session.refresh(medication)
    assert medication.end_date == local_today() - timedelta(days=1)
    assert db.session.get(MedicationLog, log.log_id) is not None         # history kept
    patient_client = client.application.test_client()
    login(patient_client, "patient", patient.email)
    today = patient_client.get("/patient/today").get_data(as_text=True)
    assert "Pramipexole" not in today.split("Log an extra dose")[0]      # no dose rows today
    meds = patient_client.get("/patient/medications").get_data(as_text=True)
    assert "Stopped medications (1)" in meds


def test_patient_sees_who_prescribed_it(client, hcp, patient, medication):
    medication.prescribed_by_hcp_id = hcp.hcp_id
    db.session.commit()
    login(client, "patient", patient.email)
    assert f"Prescribed by {hcp.name}" in client.get("/patient/medications").get_data(as_text=True)


def test_caregiver_view_is_read_only_for_medications(client, caregiver, patient, medication):
    login(client, "caregiver", caregiver.email)
    page = client.get(f"/caregiver/patient/{patient.patient_id}/dashboard").get_data(as_text=True)
    assert "Pramipexole" in page and "Prescribe" not in page and "/stop" not in page
