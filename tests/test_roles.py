"""
The everyday flows of each role that the other test files don't cover:
Admin (manage user profiles), Caregiver (sign up, link requests, observations),
HCP (application), Patient (caregiver links, medications, diagnosis year), log in / out.
"""
from datetime import date

import pytest

from conftest import login, _add
from app.extensions import db
from app.models import (
    Patient, Caregiver, CaregiverLink, CaregiverObservation, HealthcareProfessional, PatientMedication,
)


def flashes(response):
    return response.get_data(as_text=True)


# ---------------------------------------------------------------------------
# Admin: manage user profiles
# ---------------------------------------------------------------------------

def test_admin_dashboard_lists_every_account(client, admin, patient, caregiver, hcp):
    login(client, "admin", admin.email)
    page = client.get("/admin/dashboard").get_data(as_text=True)
    for name in (patient.name, caregiver.name, hcp.name):
        assert name in page


def test_admin_deactivates_and_reactivates_a_patient(client, admin, patient):
    login(client, "admin", admin.email)
    response = client.post(f"/admin/patient/{patient.patient_id}/toggle", follow_redirects=True)
    assert "account is now deactivated" in flashes(response)
    client.post(f"/admin/patient/{patient.patient_id}/toggle")
    db.session.refresh(patient)
    assert patient.account_status == "active"


def test_admin_deactivates_a_caregiver(client, admin, caregiver):
    login(client, "admin", admin.email)
    client.post(f"/admin/caregiver/{caregiver.caregiver_id}/toggle")
    db.session.refresh(caregiver)
    assert caregiver.account_status == "deactivated"


@pytest.mark.parametrize("decision, status", [("approve", "approved"), ("reject", "rejected")])
def test_admin_decides_on_an_hcp_application(client, admin, decision, status):
    applicant = _add(HealthcareProfessional(name="Dr New", email="new.hcp@test.local",
                                            institution="X", credential_status="pending"))
    login(client, "admin", admin.email)
    response = client.post(f"/admin/hcp/{applicant.hcp_id}/decide", data={"decision": decision},
                           follow_redirects=True)
    assert f"application was {status}" in flashes(response)
    db.session.refresh(applicant)
    assert applicant.credential_status == status


def test_admin_assigns_a_patient_to_an_approved_hcp_only(client, admin, other_patient, hcp):
    pending = _add(HealthcareProfessional(name="Dr Pending", email="pending.hcp@test.local",
                                          institution="X", credential_status="pending"))
    login(client, "admin", admin.email)

    response = client.post(f"/admin/patient/{other_patient.patient_id}/assign",
                           data={"hcp_id": pending.hcp_id}, follow_redirects=True)
    assert "Only approved healthcare professionals" in flashes(response)
    db.session.refresh(other_patient)
    assert other_patient.hcp_id is None

    client.post(f"/admin/patient/{other_patient.patient_id}/assign", data={"hcp_id": hcp.hcp_id})
    db.session.refresh(other_patient)
    assert other_patient.hcp_id == hcp.hcp_id


def test_admin_actions_on_a_missing_account_give_404(client, admin):
    login(client, "admin", admin.email)
    assert client.post("/admin/patient/99999/toggle").status_code == 404
    assert client.post("/admin/hcp/99999/decide", data={"decision": "approve"}).status_code == 404


# ---------------------------------------------------------------------------
# Caregiver: sign up, link requests, observations
# ---------------------------------------------------------------------------

SIGN_UP = {"name": "New Carer", "email": "carer.new@test.local", "password": "Passw0rd1",
           "confirm_password": "Passw0rd1"}


def test_caregiver_signs_up_and_can_log_in(client):
    response = client.post("/caregiver/register", data=SIGN_UP, follow_redirects=True)
    assert "Account created" in flashes(response)
    login(client, "caregiver", SIGN_UP["email"])
    assert client.get("/caregiver/dashboard").status_code == 200


def test_caregiver_sign_up_refuses_a_taken_email_and_a_weak_password(client, caregiver):
    taken = client.post("/caregiver/register", data={**SIGN_UP, "email": caregiver.email}, follow_redirects=True)
    assert "already exists" in flashes(taken)
    weak = client.post("/caregiver/register", data={**SIGN_UP, "password": "short", "confirm_password": "short"},
                       follow_redirects=True)
    assert "at least 8" in flashes(weak)
    assert Caregiver.query.count() == 1                   # only the fixture's caregiver


@pytest.mark.parametrize("decision, status", [("approve", "approved"), ("decline", "declined")])
def test_caregiver_answers_a_link_request(client, other_patient, decision, status):
    carer = _add(Caregiver(name="Carer Two", email="two@test.local"))
    link = CaregiverLink(patient_id=other_patient.patient_id, caregiver_id=carer.caregiver_id, status="pending")
    db.session.add(link)
    db.session.commit()
    login(client, "caregiver", carer.email)
    assert other_patient.name in client.get("/caregiver/requests").get_data(as_text=True)

    client.post("/caregiver/requests", data={"link_id": link.link_id, "decision": decision})
    db.session.refresh(link)
    assert link.status == status


def test_caregiver_dashboard_lists_linked_patients(client, caregiver, patient):
    login(client, "caregiver", caregiver.email)
    page = client.get("/caregiver/dashboard").get_data(as_text=True)
    assert patient.name in page


def test_caregiver_observation_is_saved_and_shown_to_the_hcp(client, caregiver, patient, hcp):
    login(client, "caregiver", caregiver.email)
    assert client.get(f"/caregiver/patient/{patient.patient_id}/observe").status_code == 200
    client.post(f"/caregiver/patient/{patient.patient_id}/observe", data={"note": "Shuffling more today."})
    obs = CaregiverObservation.query.one()
    assert (obs.patient_id, obs.note) == (patient.patient_id, "Shuffling more today.")
    assert obs.timestamp is not None

    hcp_client = client.application.test_client()
    login(hcp_client, "hcp", hcp.email)
    assert "Shuffling more today." in hcp_client.get(f"/hcp/patient/{patient.patient_id}").get_data(as_text=True)


# ---------------------------------------------------------------------------
# HCP: application
# ---------------------------------------------------------------------------

def test_hcp_application_starts_pending(client):
    response = client.post("/hcp/register", data={**SIGN_UP, "email": "dr.new@test.local",
                                                  "institution": "Test Hospital"}, follow_redirects=True)
    assert "Application submitted" in flashes(response)
    assert HealthcareProfessional.query.filter_by(email="dr.new@test.local").one().credential_status == "pending"


def test_hcp_application_refuses_a_taken_email(client, hcp):
    response = client.post("/hcp/register", data={**SIGN_UP, "email": hcp.email, "institution": "X"},
                           follow_redirects=True)
    assert "already exists" in flashes(response)


def test_hcp_dashboard_lists_assigned_patients(client, hcp, patient):
    login(client, "hcp", hcp.email)
    assert patient.name in client.get("/hcp/dashboard").get_data(as_text=True)


# ---------------------------------------------------------------------------
# Patient: caregiver links, medications, diagnosis year
# ---------------------------------------------------------------------------

def test_patient_sends_a_caregiver_request(client, patient):
    carer = _add(Caregiver(name="Carer Three", email="three@test.local"))
    login(client, "patient", patient.email)

    response = client.post("/patient/caregivers", data={"caregiver_email": "nobody@test.local"}, follow_redirects=True)
    assert "No caregiver account found" in flashes(response)

    client.post("/patient/caregivers", data={"caregiver_email": carer.email})
    link = CaregiverLink.query.filter_by(caregiver_id=carer.caregiver_id).one()
    assert link.status == "pending"

    again = client.post("/patient/caregivers", data={"caregiver_email": carer.email}, follow_redirects=True)
    assert "already pending" in flashes(again)


def test_declined_request_can_be_sent_again(client, patient):
    carer = _add(Caregiver(name="Carer Four", email="four@test.local"))
    link = CaregiverLink(patient_id=patient.patient_id, caregiver_id=carer.caregiver_id, status="declined")
    db.session.add(link)
    db.session.commit()
    login(client, "patient", patient.email)
    response = client.post("/patient/caregivers", data={"caregiver_email": carer.email}, follow_redirects=True)
    assert "re-sent" in flashes(response)
    db.session.refresh(link)
    assert link.status == "pending"


def test_patient_cannot_revoke_someone_elses_link(client, patient, other_patient):
    carer = _add(Caregiver(name="Carer Five", email="five@test.local"))
    link = CaregiverLink(patient_id=other_patient.patient_id, caregiver_id=carer.caregiver_id, status="approved")
    db.session.add(link)
    db.session.commit()
    login(client, "patient", patient.email)
    client.post(f"/patient/caregivers/{link.link_id}/revoke")
    assert db.session.get(CaregiverLink, link.link_id) is not None


def test_patient_removes_own_medication_but_not_anothers(client, patient, other_patient, medication):
    login(client, "patient", other_patient.email)
    client.post(f"/patient/medications/{medication.patient_medication_id}/remove")
    assert db.session.get(PatientMedication, medication.patient_medication_id) is not None

    login(client, "patient", patient.email)
    client.post(f"/patient/medications/{medication.patient_medication_id}/remove")
    assert db.session.get(PatientMedication, medication.patient_medication_id) is None


def test_patient_sets_diagnosis_year(client, other_patient):
    login(client, "patient", other_patient.email)
    bad = client.post("/patient/diagnosis-year", data={"diagnosis_year": date.today().year + 1},
                      follow_redirects=True)
    db.session.refresh(other_patient)
    assert other_patient.diagnosis_year is None and bad.status_code == 200

    client.post("/patient/diagnosis-year", data={"diagnosis_year": 2020})
    db.session.refresh(other_patient)
    assert other_patient.diagnosis_year == 2020


def test_patient_sign_up_refuses_a_taken_email(client, patient):
    response = client.post("/patient/register", data={**SIGN_UP, "email": patient.email,
                                                      "date_of_birth": "1950-01-01"}, follow_redirects=True)
    assert "already exists" in flashes(response)
    assert Patient.query.count() == 1


# ---------------------------------------------------------------------------
# Log in / out
# ---------------------------------------------------------------------------

def test_unknown_role_is_refused(client, patient):
    response = client.post("/auth/login", data={"role": "superuser", "email": patient.email,
                                                "password": "Passw0rd1"}, follow_redirects=True)
    assert "Invalid role" in flashes(response)


def test_log_out_ends_the_session(client, patient):
    login(client, "patient", patient.email)
    assert client.get("/patient/dashboard").status_code == 200
    client.get("/auth/logout")
    response = client.get("/patient/dashboard")
    assert response.status_code == 302 and "/auth/login" in response.headers["Location"]
