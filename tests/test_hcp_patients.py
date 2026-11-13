"""
Step 4 of the 80% check: an HCP lists their patients' emails at sign-up; the Admin sees which
have accounts and approving the HCP assigns them. The applicant is never told whether an email
belongs to a patient (privacy).
"""
import pytest

from conftest import login, _add
from app.extensions import db
from app.models import HealthcareProfessional, Patient
from app.validators import parse_patient_emails

SIGN_UP = {"name": "Dr New", "email": "dr.new@test.local", "institution": "Test Hospital",
           "password": "Passw0rd1", "confirm_password": "Passw0rd1"}


def _apply(client, patient_emails):
    return client.post("/hcp/register", data={**SIGN_UP, "patient_emails": patient_emails},
                       follow_redirects=True)


def _applicant():
    return HealthcareProfessional.query.filter_by(email=SIGN_UP["email"]).one()


# --- parsing the box -------------------------------------------------------------------------

def test_emails_split_on_lines_commas_and_spaces_lowercased_and_deduplicated():
    error, emails = parse_patient_emails("Amina@Test.local, peter@test.local\npeter@test.local;  x@y.org")
    assert error is None
    assert emails == ["amina@test.local", "peter@test.local", "x@y.org"]


def test_blank_box_is_fine():
    assert parse_patient_emails("   ") == (None, [])


@pytest.mark.parametrize("raw", ["not-an-email", "amina@", "a@b"])
def test_something_that_isnt_an_email_is_refused(raw):
    error, emails = parse_patient_emails(raw)
    assert emails is None and "doesn't look like an email" in error


# --- sign-up --------------------------------------------------------------------------------

def test_listed_emails_are_saved_with_the_application(client, patient):
    _apply(client, f"{patient.email}\nunknown@test.local")
    assert _applicant().requested_emails() == [patient.email, "unknown@test.local"]


def test_applicant_is_never_told_whether_an_email_has_an_account(client, patient):
    page = _apply(client, f"{patient.email}, unknown@test.local").get_data(as_text=True)
    assert "Application submitted" in page
    assert patient.name not in page and "No account" not in page


def test_bad_email_in_the_box_saves_nothing(client):
    page = _apply(client, "amina-at-home").get_data(as_text=True)
    assert "look like an email address" in page
    assert HealthcareProfessional.query.filter_by(email=SIGN_UP["email"]).count() == 0


# --- admin approval ---------------------------------------------------------------------------

def test_admin_sees_what_each_listed_email_matches(client, admin, patient, other_patient, hcp):
    _apply(client, f"{other_patient.email}\n{patient.email}\nnobody@test.local")
    login(client, "admin", admin.email)
    page = client.get("/admin/dashboard").get_data(as_text=True)
    assert f"{other_patient.name}: will be assigned" in page          # no clinician yet
    assert f"{patient.name}: already with {hcp.name}" in page         # has a clinician
    assert "No account with this email" in page


def test_approving_assigns_listed_patients_without_a_clinician(client, admin, patient, other_patient, hcp):
    _apply(client, f"{other_patient.email}\n{patient.email}\nnobody@test.local")
    login(client, "admin", admin.email)
    applicant = _applicant()

    response = client.post(f"/admin/hcp/{applicant.hcp_id}/decide", data={"decision": "approve"},
                           follow_redirects=True)

    assert f"Assigned to them: {other_patient.name}" in response.get_data(as_text=True)
    db.session.refresh(other_patient)
    db.session.refresh(patient)
    assert other_patient.hcp_id == applicant.hcp_id
    assert patient.hcp_id == hcp.hcp_id                               # kept: "Reassign" not ticked


def test_ticked_reassign_moves_a_patient_to_the_new_hcp(client, admin, patient, hcp):
    _apply(client, patient.email)
    login(client, "admin", admin.email)
    applicant = _applicant()
    client.post(f"/admin/hcp/{applicant.hcp_id}/decide",
                data={"decision": "approve", "reassign": patient.patient_id})
    db.session.refresh(patient)
    assert patient.hcp_id == applicant.hcp_id


def test_rejected_application_assigns_nobody(client, admin, other_patient):
    _apply(client, other_patient.email)
    login(client, "admin", admin.email)
    client.post(f"/admin/hcp/{_applicant().hcp_id}/decide", data={"decision": "reject"})
    db.session.refresh(other_patient)
    assert other_patient.hcp_id is None


def test_emails_match_whatever_the_capitals(client, admin):
    mixed = _add(Patient(name="Mixed Case", email="Mixed.Case@Test.local",
                         date_of_birth=__import__("datetime").date(1960, 1, 1)))
    _apply(client, "mixed.case@test.local")
    login(client, "admin", admin.email)
    client.post(f"/admin/hcp/{_applicant().hcp_id}/decide", data={"decision": "approve"})
    db.session.refresh(mixed)
    assert mixed.hcp_id == _applicant().hcp_id


def test_approved_hcp_then_sees_the_assigned_patient(client, admin, other_patient):
    _apply(client, other_patient.email)
    login(client, "admin", admin.email)
    client.post(f"/admin/hcp/{_applicant().hcp_id}/decide", data={"decision": "approve"})
    hcp_client = client.application.test_client()
    login(hcp_client, "hcp", SIGN_UP["email"])
    assert other_patient.name in hcp_client.get("/hcp/dashboard").get_data(as_text=True)
