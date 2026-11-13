"""
Shared form validation used by the Patient, Caregiver and HCP register routes.
"""
import re

MIN_PASSWORD_LENGTH = 8


def password_error(password, confirm):
    """Return an error message if the password is unacceptable, otherwise None."""
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
    if not any(c.isalpha() for c in password) or not any(c.isdigit() for c in password):
        return "Password must contain at least one letter and one number."
    if password != confirm:
        return "Passwords do not match."
    return None


_EMAIL = re.compile(r"[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+")
MAX_PATIENT_EMAILS = 20


def parse_patient_emails(raw):
    """The "Patients you'll be monitoring" box on the HCP sign-up (80% check).
    Emails separated by new lines, commas, semicolons or spaces; blank is fine (optional).
    Returns (error, None) or (None, [unique lower-case emails]). Says nothing about whether an
    email has an account (privacy): the Admin checks that when approving."""
    emails = []
    for token in re.split(r"[\s,;]+", (raw or "").strip()):
        if not token:
            continue
        if not _EMAIL.fullmatch(token):
            return f"\"{token}\" doesn't look like an email address. Check the patient emails.", None
        if token.lower() not in emails:
            emails.append(token.lower())
    if len(emails) > MAX_PATIENT_EMAILS:
        return f"Please list at most {MAX_PATIENT_EMAILS} patient emails.", None
    return None, emails
