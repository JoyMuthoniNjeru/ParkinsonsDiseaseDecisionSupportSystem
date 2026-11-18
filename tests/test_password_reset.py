"""
Password reset by email (18 Nov): Forgot password -> emailed link (30 min, works once) -> new
password. Tests never send real email: MAIL_SERVER is off, so app.mail puts emails in `outbox`.
"""
import re

import pytest
from itsdangerous import URLSafeTimedSerializer

from conftest import login, PASSWORD
from app import mail
from app.extensions import db

NEW = "NewPassw0rd"


@pytest.fixture(autouse=True)
def empty_outbox():
    mail.outbox.clear()
    yield
    mail.outbox.clear()


def forgot(client, role, email):
    return client.post("/auth/forgot", data={"role": role, "email": email}, follow_redirects=True)


def reset_link():
    """The /auth/reset/... path from the one email that was sent."""
    assert len(mail.outbox) == 1
    return re.search(r"http://[^/]+(/auth/reset/\S+)", mail.outbox[0]["body"]).group(1)


def set_new_password(client, link, password=NEW, confirm=None):
    return client.post(link, data={"password": password, "confirm_password": confirm or password},
                       follow_redirects=True)


def test_login_page_links_to_forgot_password(client):
    assert 'href="/auth/forgot"' in client.get("/auth/login").get_data(as_text=True)
    assert client.get("/auth/forgot").status_code == 200


@pytest.mark.parametrize("role, fixture", [("patient", "patient"), ("caregiver", "caregiver"), ("hcp", "hcp")])
def test_each_role_can_reset_with_the_emailed_link(client, request, role, fixture):
    user = request.getfixturevalue(fixture)
    response = forgot(client, role, user.email)
    assert b"we&#39;ve sent it a link" in response.data
    assert mail.outbox[0]["to"] == user.email and user.name in mail.outbox[0]["body"]

    link = reset_link()
    assert client.get(link).status_code == 200
    response = set_new_password(client, link)
    assert b"Your password has been changed" in response.data

    assert login(client, role, user.email, PASSWORD).headers["Location"].endswith("/auth/login")   # old refused
    assert login(client, role, user.email, NEW).headers["Location"].endswith("/dashboard")         # new works


def test_email_capitals_and_spaces_dont_matter(client, patient):
    forgot(client, "patient", "  PATIENT@Test.Local ")
    assert len(mail.outbox) == 1


def test_unknown_email_gets_the_same_answer_and_no_email(client, patient):
    known = forgot(client, "patient", patient.email).get_data(as_text=True)
    mail.outbox.clear()
    unknown = forgot(client, "patient", "nobody@test.local").get_data(as_text=True)
    wrong_role = forgot(client, "caregiver", patient.email).get_data(as_text=True)   # patient, not a caregiver
    assert mail.outbox == []
    message = "If there is a patient account with that email"
    assert message in known and message in unknown
    assert "If there is a caregiver account with that email" in wrong_role


def test_deactivated_and_unapproved_accounts_get_no_email(client, patient, hcp):
    patient.account_status = "deactivated"
    hcp.credential_status = "pending"
    db.session.commit()
    forgot(client, "patient", patient.email)
    forgot(client, "hcp", hcp.email)
    assert mail.outbox == []


def test_admin_cant_use_forgot_password(client, admin):
    response = forgot(client, "admin", admin.email)
    assert b"Please choose Patient, Caregiver or Clinician" in response.data
    assert mail.outbox == []


def test_link_works_only_once(client, patient):
    forgot(client, "patient", patient.email)
    link = reset_link()
    set_new_password(client, link)
    response = client.get(link, follow_redirects=True)
    assert b"expired or has already been used" in response.data
    set_new_password(client, link, password="Another1pass")             # refused, nothing changes
    db.session.refresh(patient)
    assert patient.check_password(NEW)


def test_an_older_link_stops_working_after_a_reset(client, patient):
    forgot(client, "patient", patient.email)
    first = reset_link()
    mail.outbox.clear()
    forgot(client, "patient", patient.email)
    set_new_password(client, reset_link())
    assert b"expired or has already been used" in client.get(first, follow_redirects=True).data


def test_link_expires_after_30_minutes(client, patient, monkeypatch):
    forgot(client, "patient", patient.email)
    link = reset_link()
    # Pretend the link is being opened 31 minutes later
    import time
    real_time = time.time
    monkeypatch.setattr(time, "time", lambda: real_time() + 31 * 60)
    assert b"expired or has already been used" in client.get(link, follow_redirects=True).data


def test_made_up_or_edited_links_are_refused(client, app, patient):
    forged = URLSafeTimedSerializer("not-the-secret-key", salt="password-reset").dumps(
        {"role": "patient", "id": patient.patient_id, "fp": "x"})
    for token in ("nonsense", forged):
        assert b"expired or has already been used" in client.get(f"/auth/reset/{token}", follow_redirects=True).data


def test_new_password_follows_the_sign_up_rules(client, patient):
    forgot(client, "patient", patient.email)
    link = reset_link()
    assert b"at least 8 characters" in set_new_password(client, link, password="short1").data
    assert b"one letter and one number" in set_new_password(client, link, password="onlyletters").data
    assert b"do not match" in set_new_password(client, link, confirm="Different1").data
    db.session.refresh(patient)
    assert patient.check_password(PASSWORD)                             # unchanged
    assert b"Your password has been changed" in set_new_password(client, link).data   # link still usable


# ---------------------------------------------------------------------------
# Change password while logged in (every role, incl. Admin)
# ---------------------------------------------------------------------------

def change(client, current=PASSWORD, new=NEW, confirm=None):
    return client.post("/auth/password", data={"current_password": current, "password": new,
                                               "confirm_password": confirm or new}, follow_redirects=True)


@pytest.mark.parametrize("role, fixture", [("patient", "patient"), ("caregiver", "caregiver"),
                                           ("hcp", "hcp"), ("admin", "admin")])
def test_every_role_can_change_their_password(client, request, role, fixture):
    user = request.getfixturevalue(fixture)
    login(client, role, user.email)
    page = client.get("/auth/password").get_data(as_text=True)
    assert "Change password" in page and 'href="/auth/password"' in page      # navbar link too
    assert b"Your password has been changed" in change(client).data
    db.session.refresh(user)
    assert user.check_password(NEW) and not user.check_password(PASSWORD)


def test_wrong_current_password_or_bad_new_one_changes_nothing(client, patient):
    login(client, "patient", patient.email)
    assert b"current password isn&#39;t right" in change(client, current="Wrong1234").data
    assert b"at least 8 characters" in change(client, new="short1").data
    assert b"do not match" in change(client, confirm="Different1").data
    db.session.refresh(patient)
    assert patient.check_password(PASSWORD)


def test_change_password_needs_a_log_in(client):
    assert client.get("/auth/password").headers["Location"].startswith("/auth/login")


def test_changing_the_password_kills_an_unused_reset_link(client, patient):
    forgot(client, "patient", patient.email)
    link = reset_link()
    login(client, "patient", patient.email)
    change(client)
    assert b"expired or has already been used" in client.get(link, follow_redirects=True).data


# ---------------------------------------------------------------------------
# app/mail.py with a mail server set: a FAKE smtplib.SMTP (nothing leaves the laptop)
# ---------------------------------------------------------------------------

class FakeSMTP:
    sent = []

    def __init__(self, server, port, timeout=None):
        self.server, self.port = server, port

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def starttls(self, context=None):
        self.tls = True

    def login(self, username, password):
        self.login_as = (username, password)

    def send_message(self, msg):
        FakeSMTP.sent.append((self, msg))


@pytest.fixture
def fake_smtp(app, monkeypatch):
    FakeSMTP.sent = []
    monkeypatch.setattr(mail.smtplib, "SMTP", FakeSMTP)
    for key, value in {"MAIL_SERVER": "smtp.example.com", "MAIL_PORT": 587, "MAIL_USERNAME": "pd@example.com",
                       "MAIL_PASSWORD": "abcd efgh ijkl mnop", "MAIL_SENDER": None}.items():
        monkeypatch.setitem(app.config, key, value)
    return FakeSMTP


def test_send_email_uses_tls_and_the_app_password_without_spaces(app, fake_smtp):
    mail.send_email("someone@example.com", "Hi", "Body text")
    smtp, msg = fake_smtp.sent[0]
    assert (smtp.server, smtp.port, smtp.tls) == ("smtp.example.com", 587, True)
    assert smtp.login_as == ("pd@example.com", "abcdefghijklmnop")
    assert (msg["To"], msg["Subject"], msg["From"]) == ("someone@example.com", "Hi", "PD Compass <pd@example.com>")
    assert "Body text" in msg.get_content()


def test_background_sending_never_breaks_the_page(app, fake_smtp, monkeypatch):
    monkeypatch.setitem(app.config, "TESTING", False)        # use the real background thread
    def broken(*args, **kwargs):
        raise OSError("no internet")
    monkeypatch.setattr(mail.smtplib, "SMTP", broken)
    threads = []
    real_thread = mail.threading.Thread
    monkeypatch.setattr(mail.threading, "Thread", lambda **kw: threads.append(real_thread(**kw)) or threads[-1])
    mail.send_email_in_background("someone@example.com", "Hi", "Body")   # doesn't raise
    threads[0].join(5)
    assert not threads[0].is_alive()
