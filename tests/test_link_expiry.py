"""
Step 3 of the 80% check: a caregiver link request that isn't answered within 24 h expires.
Approved links never expire. The Scheduler calls expire_old_requests() every 15 min; the pages
call it too, so an old request can never be approved in between.
"""
from datetime import datetime, timedelta

from conftest import login, _add
from app.extensions import db
from app.links import expire_old_requests
from app.models import Caregiver, CaregiverLink


def _request(patient, hours_old, status="pending", email="new.carer@test.local"):
    carer = _add(Caregiver(name="New Carer", email=email))
    link = CaregiverLink(patient_id=patient.patient_id, caregiver_id=carer.caregiver_id, status=status,
                         linked_date=datetime.utcnow() - timedelta(hours=hours_old))
    db.session.add(link)
    db.session.commit()
    return carer, link


def test_request_23_hours_old_is_still_pending(patient):
    _, link = _request(patient, hours_old=23)
    assert expire_old_requests() == 0
    db.session.refresh(link)
    assert link.status == "pending"


def test_request_25_hours_old_expires(patient):
    _, link = _request(patient, hours_old=25)
    assert expire_old_requests() == 1
    db.session.refresh(link)
    assert link.status == "expired"


def test_expiry_uses_the_time_it_is_given(patient):
    """Same request, judged at two fixed times (what the Scheduler would see later)."""
    _, link = _request(patient, hours_old=1)
    assert expire_old_requests(now=datetime.utcnow() + timedelta(hours=22)) == 0
    assert expire_old_requests(now=datetime.utcnow() + timedelta(hours=24)) == 1


def test_approved_links_never_expire(caregiver, patient):
    link = CaregiverLink.query.filter_by(caregiver_id=caregiver.caregiver_id).one()
    link.linked_date = datetime.utcnow() - timedelta(days=30)
    db.session.commit()
    assert expire_old_requests() == 0
    db.session.refresh(link)
    assert link.status == "approved"


def test_caregiver_cannot_approve_an_old_request_even_before_the_job_runs(client, patient):
    carer, link = _request(patient, hours_old=30)        # still "pending" in the database
    login(client, "caregiver", carer.email)
    assert patient.name not in client.get("/caregiver/requests").get_data(as_text=True).split("Waiting for you")[1].split("</section>")[0]

    response = client.post("/caregiver/requests", data={"link_id": link.link_id, "decision": "approve"},
                           follow_redirects=True)

    assert b"has expired or was already answered" in response.data
    db.session.refresh(link)
    assert link.status == "expired"
    assert client.get(f"/caregiver/patient/{patient.patient_id}/dashboard").status_code == 404


def test_patient_sees_expired_and_can_resend(client, patient):
    carer, link = _request(patient, hours_old=30)
    login(client, "patient", patient.email)
    page = client.get("/patient/caregivers").get_data(as_text=True)
    assert "Expired" in page and "Re-send" in page

    response = client.post("/patient/caregivers", data={"caregiver_email": carer.email}, follow_redirects=True)

    assert b"re-sent" in response.data
    db.session.refresh(link)
    assert link.status == "pending"
    assert datetime.utcnow() - link.linked_date < timedelta(minutes=1)   # a fresh 24 hours


def test_pending_request_shows_when_it_expires(client, patient):
    carer, _ = _request(patient, hours_old=2)
    login(client, "caregiver", carer.email)
    assert "answer by" in client.get("/caregiver/requests").get_data(as_text=True)
