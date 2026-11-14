"""
Step 7 of the 80% check: "Add dose reminders to my calendar" downloads an .ics file with one daily
repeating event (and an alert) per dose time of each CURRENT medication, for the patient only.
"""
from datetime import date, timedelta

from conftest import login, local_today
from app.extensions import db
from app.calendar_file import _fold


def _unfold(text):
    """Undo line folding, as a calendar app does."""
    return text.replace("\r\n ", "")


def _download(client, patient):
    login(client, "patient", patient.email)
    return client.get("/patient/medications/calendar.ics")


def test_calendar_downloads_as_an_ics_file(client, patient, medication):
    response = _download(client, patient)
    assert response.status_code == 200 and response.mimetype == "text/calendar"
    assert "attachment" in response.headers["Content-Disposition"]
    text = response.get_data(as_text=True)
    assert text.startswith("BEGIN:VCALENDAR\r\n") and text.endswith("END:VCALENDAR\r\n")


def test_one_daily_event_with_an_alert_per_dose_time(client, patient, medication):
    text = _unfold(_download(client, patient).get_data(as_text=True))
    assert text.count("BEGIN:VEVENT") == 3                          # 08:00, 13:00, 18:00
    assert text.count("RRULE:FREQ=DAILY") == 3 and text.count("TRIGGER:PT0M") == 3
    assert "SUMMARY:Take Pramipexole 0.25 mg (1 tablet)" in text
    first_day = max(medication.started_date, local_today())
    assert f"DTSTART;TZID=Africa/Nairobi:{first_day:%Y%m%d}T130000" in text
    uids = [line for line in text.split("\r\n") if line.startswith("UID:")]
    assert len(set(uids)) == 3                                      # stable, one per schedule


def test_end_date_becomes_until_in_utc(client, patient, medication):
    medication.end_date = local_today() + timedelta(days=10)
    db.session.commit()
    text = _unfold(_download(client, patient).get_data(as_text=True))
    assert f"UNTIL={medication.end_date:%Y%m%d}T205959Z" in text    # 23:59:59 Nairobi = 20:59:59 UTC


def test_stopped_medications_are_left_out(client, patient, medication):
    medication.end_date = local_today() - timedelta(days=1)
    db.session.commit()
    assert "BEGIN:VEVENT" not in _download(client, patient).get_data(as_text=True)


def test_special_characters_are_escaped(client, patient, medication):
    medication.instructions = "Take with food, not milk; swallow whole"
    db.session.commit()
    text = _unfold(_download(client, patient).get_data(as_text=True))
    assert "Take with food\\, not milk\\; swallow whole" in text


def test_long_lines_are_folded_to_75_octets():
    folded = _fold("DESCRIPTION:" + "é" * 100)
    assert all(len(line.encode("utf-8")) <= 75 for line in folded.split("\r\n"))
    assert _unfold(folded) == "DESCRIPTION:" + "é" * 100


def test_only_the_patient_can_download_it(client, caregiver, hcp):
    for role, user in (("caregiver", caregiver), ("hcp", hcp)):
        c = client.application.test_client()
        login(c, role, user.email)
        response = c.get("/patient/medications/calendar.ics")
        assert response.status_code == 302 and "/auth/login" in response.headers["Location"], role


def test_button_shows_on_my_medications(client, patient, medication):
    login(client, "patient", patient.email)
    assert "Add dose reminders to my calendar" in client.get("/patient/medications").get_data(as_text=True)
