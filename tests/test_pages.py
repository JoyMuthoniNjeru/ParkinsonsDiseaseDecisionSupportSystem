"""
Patient home (big tiles) and the pages the tiles open: Today's doses, My trends, Alerts.
Actions send ?next=<page> so the patient lands back where they tapped.
"""
from datetime import datetime, timedelta

from conftest import login, make_log, make_symptom, UTC_OFFSET
from app.extensions import db
from app.models import Alert, Insight, MedicationSchedule
from app.rule_engine import evaluate_medication_log


def _due_now(medication):
    """A schedule that is 'Due now' whatever time the tests run (10 min from now, local)."""
    local_now = datetime.utcnow() + UTC_OFFSET
    s = MedicationSchedule(patient_medication_id=medication.patient_medication_id,
                           dose_time=(local_now + timedelta(minutes=10)).time().replace(second=0, microsecond=0))
    db.session.add(s)
    db.session.commit()
    return s


def test_home_shows_the_four_tiles(client, patient):
    login(client, "patient", patient.email)
    page = client.get("/patient/dashboard").get_data(as_text=True)
    for tile in ("Today's doses", "How are you feeling?", "My trends", "Alerts"):
        assert tile in page
    for url in ("/patient/today", "/patient/report", "/patient/trends", "/patient/alerts"):
        assert f'href="{url}"' in page


def test_due_dose_can_be_logged_from_home_and_returns_home(client, patient, medication):
    schedule = _due_now(medication)
    login(client, "patient", patient.email)
    page = client.get("/patient/dashboard").get_data(as_text=True)
    assert "Due now" in page and 'value="taken"' in page
    response = client.post("/patient/doses/log",
                           data={"schedule_id": schedule.schedule_id, "status": "taken", "next": "dashboard"})
    assert response.headers["Location"].endswith("/patient/dashboard")


def test_dose_logged_on_the_today_page_returns_there(client, patient, medication):
    schedule = _due_now(medication)
    login(client, "patient", patient.email)
    assert "Log an extra dose" in client.get("/patient/today").get_data(as_text=True)
    response = client.post("/patient/doses/log",
                           data={"schedule_id": schedule.schedule_id, "status": "missed", "next": "today"})
    assert response.headers["Location"].endswith("/patient/today")


def test_unknown_next_value_falls_back_safely(client, patient, medication):
    """next is a page NAME from a fixed list, never a URL, so it can't send anyone off-site."""
    schedule = _due_now(medication)
    login(client, "patient", patient.email)
    response = client.post("/patient/doses/log", data={"schedule_id": schedule.schedule_id,
                                                       "status": "taken", "next": "https://evil.example"})
    assert response.headers["Location"].endswith("/patient/today")


def test_trends_page_has_chart_entries_and_diagnosis_year_but_no_insights(client, patient, tremor):
    make_symptom(patient, tremor, 2)
    db.session.add(Insight(patient_id=patient.patient_id, content="Tremor: stable recently."))
    db.session.commit()
    login(client, "patient", patient.email)
    page = client.get("/patient/trends").get_data(as_text=True)
    assert "Symptom trend" in page and "Recent symptoms" in page and "Year of diagnosis" in page
    assert "Tremor: stable recently." not in page and "Update insights" not in page


def test_editing_a_symptom_returns_to_trends(client, patient, tremor):
    record = make_symptom(patient, tremor, 2)
    login(client, "patient", patient.email)
    response = client.post(f"/patient/symptoms/{record.record_id}/edit", data={"severity": 1})
    assert response.headers["Location"].endswith("/patient/trends")


def test_alerts_page_lists_the_patients_alerts(client, patient, medication):
    evaluate_medication_log(make_log(medication, "missed", days_ago=1))
    login(client, "patient", patient.email)
    page = client.get("/patient/alerts").get_data(as_text=True)
    assert "Missed dose: Pramipexole 0.25 mg at 13:00" in page


def test_other_roles_cannot_open_the_new_patient_pages(client, caregiver):
    login(client, "caregiver", caregiver.email)
    for url in ("/patient/today", "/patient/trends", "/patient/alerts"):
        response = client.get(url)
        assert response.status_code == 302 and "/auth/login" in response.headers["Location"], url


def test_text_size_toggle_only_for_patients_and_log_in(client, patient, caregiver, hcp):
    assert "Text size" in client.get("/auth/login").get_data(as_text=True)
    login(client, "patient", patient.email)
    assert "Text size" in client.get("/patient/dashboard").get_data(as_text=True)
    for role, user, url in (("caregiver", caregiver, "/caregiver/dashboard"), ("hcp", hcp, "/hcp/dashboard")):
        staff = client.application.test_client()
        login(staff, role, user.email)
        page = staff.get(url).get_data(as_text=True)
        assert "Text size" not in page and "pd-text-size" not in page, role   # no toggle, no saved size


def test_text_size_toggle_has_a_visible_label_and_hint(client, patient):
    """Feedback: say what the A / A / A buttons are for."""
    page = client.get("/auth/login").get_data(as_text=True)
    assert 'class="text-size-label"' in page and "Choose a bigger size if the text is hard to read" in page
    login(client, "patient", patient.email)
    page = client.get("/patient/dashboard").get_data(as_text=True)
    assert 'id="ts-label-nav">Text size</span>' in page and 'aria-labelledby="ts-label-nav"' in page
