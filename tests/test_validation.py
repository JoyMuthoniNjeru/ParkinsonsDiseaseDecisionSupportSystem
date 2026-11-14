"""
Server-side validation: bad input is refused even if it gets past the form (old browser,
someone editing the request). Nothing is saved when a check fails.
"""
from datetime import date, timedelta

import pytest

from conftest import login
from app.models import Patient, PatientMedication, MedicationSchedule, SymptomRecord, MedicationReference
from app.extensions import db
from app.validators import password_error


# ---------------------------------------------------------------------------
# Symptom severity (0-4 only)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad", ["5", "-1", "7", "abc", ""])
def test_symptom_score_outside_0_to_4_is_refused(client, patient, tremor, bad):
    login(client, "patient", patient.email)
    client.post("/patient/report", data={"symptom_type_id": tremor.symptom_type_id, "severity": bad})
    client.post("/patient/report", data={f"severity_{tremor.symptom_type_id}": bad})
    assert SymptomRecord.query.count() == 0


def test_one_bad_score_means_nothing_is_saved(client, patient, tremor):
    from app.models import SymptomType
    fatigue = SymptomType(name="Fatigue", category="non-motor", ppmi_code="NP1FATG", item_number="1.13")
    db.session.add(fatigue)
    db.session.commit()
    login(client, "patient", patient.email)
    client.post("/patient/report", data={f"severity_{tremor.symptom_type_id}": "2",
                                         f"severity_{fatigue.symptom_type_id}": "9"})
    assert SymptomRecord.query.count() == 0


def test_model_refuses_out_of_range_severity(app):
    with pytest.raises(ValueError):
        SymptomRecord(severity=5)


# ---------------------------------------------------------------------------
# Sign-up: date of birth, diagnosis year, password
# ---------------------------------------------------------------------------

def _sign_up(client, **overrides):
    form = {"name": "New Person", "email": "new@test.local", "password": "Passw0rd1",
            "confirm_password": "Passw0rd1", "date_of_birth": "1960-05-01"}
    form.update(overrides)
    return client.post("/patient/register", data=form, follow_redirects=True)


def test_valid_sign_up_works(client, app):
    _sign_up(client, diagnosis_year="2020")
    assert Patient.query.filter_by(email="new@test.local").one().diagnosis_year == 2020


@pytest.mark.parametrize("dob", [(date.today() + timedelta(days=1)).isoformat(), "2090-01-01",
                                 "1850-01-01", "31/02/abc", ""])
def test_bad_date_of_birth_is_refused(client, app, dob):
    response = _sign_up(client, date_of_birth=dob)
    assert b"valid date of birth" in response.data
    assert Patient.query.count() == 0


@pytest.mark.parametrize("year", [str(date.today().year + 1), "1950"])   # future / before birth (1960)
def test_bad_diagnosis_year_is_refused(client, app, year):
    _sign_up(client, diagnosis_year=year)
    assert Patient.query.count() == 0


@pytest.mark.parametrize("password, confirm, message", [
    ("short1", "short1", "at least 8"),
    ("longpassword", "longpassword", "letter and one number"),
    ("Passw0rd1", "Passw0rd2", "do not match"),
])
def test_password_rules(password, confirm, message):
    assert message in password_error(password, confirm)
    assert password_error("Passw0rd1", "Passw0rd1") is None


# ---------------------------------------------------------------------------
# Prescribe medication (the HCP does this since the 80% check; same form checks as before)
# ---------------------------------------------------------------------------

@pytest.fixture
def pramipexole(app):
    med = MedicationReference(name="Pramipexole")
    db.session.add(med)
    db.session.commit()
    return med


def _add_med(client, med, **overrides):
    form = {"medication_id": med.medication_id, "dose_amount": "0.25", "dose_unit": "mg",
            "form": "tablet", "quantity_per_dose": "1", "times_per_day": "2",
            "dose_times": ["08:00", "20:00"]}
    form.update(overrides)
    patient_id = Patient.query.filter_by(email="patient@test.local").one().patient_id
    return client.post(f"/hcp/patient/{patient_id}/medications/add", data=form, follow_redirects=True)


def test_valid_medication_is_saved_with_its_schedules(client, patient, hcp, pramipexole):
    login(client, "hcp", hcp.email)
    _add_med(client, pramipexole, quantity_per_dose="1.5")
    pm = PatientMedication.query.one()
    assert pm.prescribed_by_hcp_id == hcp.hcp_id
    assert float(pm.quantity_per_dose) == 1.5
    assert [s.dose_time.strftime("%H:%M") for s in pm.schedules] == ["08:00", "20:00"]


@pytest.mark.parametrize("overrides, message", [
    ({"quantity_per_dose": "1.3"}, b"steps of 0.5"),
    ({"quantity_per_dose": "0"}, b"steps of 0.5"),
    ({"dose_amount": "-5"}, b"enter the dose"),
    ({"form": "injection"}, b"choose a form"),
    ({"dose_unit": "kg"}, b"choose a unit"),
    ({"dose_times": ["08:00", "08:00"]}, b"different time"),
    ({"dose_times": ["08:00", "25:99"]}, b"fill in every dose time"),
    ({"times_per_day": "9"}, b"how many times a day"),
    ({"started_date": "2026-11-10", "end_date": "2026-11-01"}, b"end date can&#39;t be before"),
])
def test_bad_medication_input_is_refused(client, patient, hcp, pramipexole, overrides, message):
    login(client, "hcp", hcp.email)
    response = _add_med(client, pramipexole, **overrides)
    assert message in response.data
    assert PatientMedication.query.count() == 0
    assert MedicationSchedule.query.count() == 0


def test_unknown_medication_is_refused(client, patient, hcp, pramipexole):
    login(client, "hcp", hcp.email)
    _add_med(client, pramipexole, medication_id="9999")
    assert PatientMedication.query.count() == 0
