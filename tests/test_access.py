"""
Access control: each role only reaches its own pages and only sees the patients it's allowed to.
"""
from datetime import date

from conftest import login, _add
from app.extensions import db
from app.models import Patient, Caregiver, CaregiverLink, HealthcareProfessional


def lands_on_login(response):
    return response.status_code == 302 and "/auth/login" in response.headers["Location"]


# ---------------------------------------------------------------------------
# Logging in
# ---------------------------------------------------------------------------

def test_anonymous_visitor_is_sent_to_login(client):
    assert lands_on_login(client.get("/patient/dashboard"))
    assert lands_on_login(client.get("/hcp/dashboard"))


def test_wrong_password_is_refused(client, patient):
    response = login(client, "patient", patient.email, password="wrong-1")
    assert lands_on_login(response)
    assert lands_on_login(client.get("/patient/dashboard"))


def test_logging_in_with_the_wrong_role_is_refused(client, patient):
    """A patient's email + password under 'caregiver' doesn't log anyone in."""
    login(client, "caregiver", patient.email)
    assert lands_on_login(client.get("/caregiver/dashboard"))


def test_deactivated_patient_cannot_log_in(client, patient):
    patient.account_status = "deactivated"
    db.session.commit()
    response = client.post("/auth/login", data={"role": "patient", "email": patient.email,
                                                "password": "Passw0rd1"}, follow_redirects=True)
    assert b"This account has been deactivated" in response.data
    assert lands_on_login(client.get("/patient/dashboard"))


def test_patient_deactivated_while_logged_in_is_logged_out(client, patient):
    login(client, "patient", patient.email)
    assert client.get("/patient/dashboard").status_code == 200
    patient.account_status = "deactivated"
    db.session.commit()
    assert lands_on_login(client.get("/patient/dashboard"))


def test_pending_and_rejected_hcp_cannot_log_in(client, app):
    for status in ("pending", "rejected"):
        hcp = _add(HealthcareProfessional(name=f"Dr {status}", email=f"{status}@test.local",
                                          institution="X", credential_status=status))
        response = client.post("/auth/login", data={"role": "hcp", "email": hcp.email,
                                                    "password": "Passw0rd1"}, follow_redirects=True)
        assert f"currently {status}".encode() in response.data
        assert lands_on_login(client.get("/hcp/dashboard"))


def test_hcp_rejected_while_logged_in_is_logged_out(client, hcp):
    login(client, "hcp", hcp.email)
    assert client.get("/hcp/dashboard").status_code == 200
    hcp.credential_status = "rejected"
    db.session.commit()
    assert lands_on_login(client.get("/hcp/dashboard"))


# ---------------------------------------------------------------------------
# Roles stay on their own pages
# ---------------------------------------------------------------------------

def test_patient_cannot_open_other_roles_pages(client, patient):
    login(client, "patient", patient.email)
    for url in ("/admin/dashboard", "/hcp/dashboard", "/caregiver/dashboard"):
        assert lands_on_login(client.get(url)), url


def test_caregiver_cannot_open_patient_or_admin_pages(client, caregiver):
    login(client, "caregiver", caregiver.email)
    for url in ("/patient/dashboard", "/patient/medications", "/admin/dashboard"):
        assert lands_on_login(client.get(url)), url


def test_hcp_cannot_use_admin_actions(client, hcp, patient):
    login(client, "hcp", hcp.email)
    assert lands_on_login(client.post(f"/admin/patient/{patient.patient_id}/toggle"))
    db.session.refresh(patient)
    assert patient.account_status == "active"


# ---------------------------------------------------------------------------
# Which patients each role can see
# ---------------------------------------------------------------------------

def test_hcp_sees_assigned_patient_but_not_others(client, hcp, patient, other_patient):
    login(client, "hcp", hcp.email)
    assert client.get(f"/hcp/patient/{patient.patient_id}").status_code == 200
    assert client.get(f"/hcp/patient/{other_patient.patient_id}").status_code == 404
    page = client.get("/hcp/dashboard").get_data(as_text=True)
    assert patient.name in page and other_patient.name not in page


def test_unassigned_patient_disappears_for_the_hcp(client, hcp, patient, admin):
    login(client, "admin", admin.email)
    client.post(f"/admin/patient/{patient.patient_id}/assign", data={"hcp_id": ""})
    hcp_client = client.application.test_client()
    login(hcp_client, "hcp", hcp.email)
    assert hcp_client.get(f"/hcp/patient/{patient.patient_id}").status_code == 404


def test_caregiver_with_approved_link_sees_the_patient(client, caregiver, patient):
    login(client, "caregiver", caregiver.email)
    assert client.get(f"/caregiver/patient/{patient.patient_id}/dashboard").status_code == 200
    assert client.get(f"/caregiver/patient/{patient.patient_id}/observe").status_code == 200


def test_caregiver_without_an_approved_link_sees_nothing(client, patient, other_patient):
    carer = _add(Caregiver(name="New Carer", email="new@test.local"))
    db.session.add(CaregiverLink(patient_id=patient.patient_id, caregiver_id=carer.caregiver_id,
                                 status="pending"))
    db.session.add(CaregiverLink(patient_id=other_patient.patient_id, caregiver_id=carer.caregiver_id,
                                 status="declined"))
    db.session.commit()
    login(client, "caregiver", carer.email)
    for p in (patient, other_patient):
        assert client.get(f"/caregiver/patient/{p.patient_id}/dashboard").status_code == 404
        assert client.post(f"/caregiver/patient/{p.patient_id}/observe",
                           data={"note": "x"}).status_code == 404


def test_revoked_caregiver_loses_access(client, caregiver, patient):
    link = CaregiverLink.query.filter_by(caregiver_id=caregiver.caregiver_id).one()
    patient_client = client.application.test_client()
    login(patient_client, "patient", patient.email)
    patient_client.post(f"/patient/caregivers/{link.link_id}/revoke")
    login(client, "caregiver", caregiver.email)
    assert client.get(f"/caregiver/patient/{patient.patient_id}/dashboard").status_code == 404


def test_caregiver_cannot_answer_someone_elses_link_request(client, caregiver, other_patient):
    stranger = _add(Caregiver(name="Stranger", email="stranger@test.local"))
    link = CaregiverLink(patient_id=other_patient.patient_id, caregiver_id=stranger.caregiver_id,
                         status="pending")
    db.session.add(link)
    db.session.commit()
    login(client, "caregiver", caregiver.email)
    client.post("/caregiver/requests", data={"link_id": link.link_id, "decision": "approve"})
    db.session.refresh(link)
    assert link.status == "pending"


# ---------------------------------------------------------------------------
# CSRF: forms must carry the hidden csrf_token (switched ON just for these tests)
# ---------------------------------------------------------------------------

import re
import pytest


@pytest.fixture
def csrf_on(app):
    app.config["WTF_CSRF_ENABLED"] = True
    yield
    app.config["WTF_CSRF_ENABLED"] = False


def _token(page_html):
    return re.search(r'name="csrf_token" value="([^"]+)"', page_html).group(1)


def test_form_without_csrf_token_is_refused(client, patient, csrf_on):
    response = client.post("/auth/login", data={"role": "patient", "email": patient.email,
                                                "password": "Passw0rd1"}, follow_redirects=True)
    assert b"That form had expired" in response.data
    assert lands_on_login(client.get("/patient/dashboard"))      # not logged in


def test_form_with_csrf_token_works(client, patient, csrf_on):
    token = _token(client.get("/auth/login").get_data(as_text=True))
    client.post("/auth/login", data={"role": "patient", "email": patient.email,
                                     "password": "Passw0rd1", "csrf_token": token})
    assert client.get("/patient/dashboard").status_code == 200


def test_logged_in_action_without_token_changes_nothing(client, patient, medication, csrf_on):
    """e.g. another website trying to mark a dose for a logged-in patient."""
    from app.models import MedicationLog
    token = _token(client.get("/auth/login").get_data(as_text=True))
    client.post("/auth/login", data={"role": "patient", "email": patient.email,
                                     "password": "Passw0rd1", "csrf_token": token})
    client.post("/patient/doses/extra", data={"patient_medication_id": medication.patient_medication_id})
    assert MedicationLog.query.count() == 0


def test_every_post_form_has_a_csrf_token():
    """Template check, so a new form can't be added without the token."""
    import pathlib
    missing = []
    for path in pathlib.Path("app/templates").rglob("*.html"):
        html = path.read_text(encoding="utf-8")
        for form in re.finditer(r"<form\b[^>]*>(.*?)</form>", html, re.S | re.I):
            if re.search(r'method="post"', form.group(0)[:400], re.I) and "csrf_token()" not in form.group(1):
                missing.append(str(path))
    assert missing == []
