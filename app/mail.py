"""
Sending email (password reset links, 18 Nov). Uses Python's built-in smtplib: no extra package.

Settings come from .env (see config.py): MAIL_SERVER, MAIL_PORT, MAIL_USERNAME, MAIL_PASSWORD,
MAIL_SENDER. With Gmail, MAIL_PASSWORD is an *App password* (Google Account -> Security ->
2-Step Verification -> App passwords), not the normal Gmail password.

Console mode: if MAIL_SERVER isn't set, nothing is sent; the email is printed in the terminal
instead. Tests always use console mode (and can read what was "sent" in `outbox`), and the app
still works offline (e.g. no internet at the demo): the reset link is in the terminal.
"""
import smtplib
import ssl
import threading
from email.message import EmailMessage

from flask import current_app

# Emails "sent" while TESTING, so tests can check them (never filled on a real server)
outbox = []


def _settings(app):
    c = app.config
    return {
        "server": c.get("MAIL_SERVER"),
        "port": c.get("MAIL_PORT", 587),
        "username": c.get("MAIL_USERNAME"),
        # Google shows app passwords in groups of 4 ("abcd efgh ..."); the spaces aren't part of it
        "password": (c.get("MAIL_PASSWORD") or "").replace(" ", ""),
        "sender": c.get("MAIL_SENDER") or c.get("MAIL_USERNAME"),
        "testing": c.get("TESTING", False),
    }


def _build(sender, to, subject, body):
    msg = EmailMessage()
    msg["From"] = f"PD Compass <{sender}>" if sender else "PD Compass"
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    return msg


def _deliver(s, to, subject, body):
    """Send one email with these settings. Raises on failure (wrong password, no internet...)."""
    if not s["server"]:
        print(f"\n * Email (console mode, not sent)\n   To: {to}\n   Subject: {subject}\n\n{body}\n",
              flush=True)
        if s["testing"]:
            outbox.append({"to": to, "subject": subject, "body": body})
        return
    msg = _build(s["sender"], to, subject, body)
    with smtplib.SMTP(s["server"], s["port"], timeout=15) as smtp:
        smtp.starttls(context=ssl.create_default_context())   # encrypt before logging in
        if s["username"]:
            smtp.login(s["username"], s["password"])
        smtp.send_message(msg)


def send_email(to, subject, body):
    """Send now and wait (used by the test-email command). Raises if it fails."""
    _deliver(_settings(current_app), to, subject, body)


def send_email_in_background(to, subject, body):
    """Send without making the page wait (~1 s for Gmail). This also means the "Forgot password"
    page answers just as fast whether or not the account exists. A failure is logged, never shown
    to the visitor (they always get the same message). Tests send straight away (console mode)."""
    app = current_app._get_current_object()
    s = _settings(app)
    if s["testing"]:
        _deliver(s, to, subject, body)
        return

    def run():
        try:
            _deliver(s, to, subject, body)
        except Exception:
            app.logger.exception("Could not send email to %s", to)

    threading.Thread(target=run, daemon=True).start()
