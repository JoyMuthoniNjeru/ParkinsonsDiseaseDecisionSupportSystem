"""
Password reset by email (18 Nov). Nothing is stored in the database (no migration):

  - The link holds a signed token = {role, user id, fingerprint of the current password hash}.
    It's signed with SECRET_KEY (itsdangerous, comes with Flask), so it can't be made up or edited.
  - It expires after 30 minutes (the signature includes the time it was made).
  - It works ONCE: after the password changes, the fingerprint no longer matches, so the same
    link (or any older one) is refused.

Only active patients / caregivers and approved clinicians can reset (admin: seed.py).
"""
import hashlib

from flask import current_app, url_for
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

from app.models import Patient, Caregiver, HealthcareProfessional
from app.extensions import db

RESET_LINK_MINUTES = 30
ROLE_MODELS = {"patient": Patient, "caregiver": Caregiver, "hcp": HealthcareProfessional}
ROLE_LABELS = {"patient": "patient", "caregiver": "caregiver", "hcp": "clinician"}


def _serializer():
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="password-reset")


def _fingerprint(user):
    return hashlib.sha256(user.password_hash.encode()).hexdigest()[:16]


def can_reset(user):
    """Deactivated accounts and pending / rejected clinicians get no reset link."""
    return (getattr(user, "account_status", "active") == "active"
            and getattr(user, "credential_status", "approved") == "approved")


def find_user(role, email):
    """The account for this role + email (capitals ignored), or None."""
    model = ROLE_MODELS.get(role)
    email = (email or "").strip().lower()
    if model is None or not email:
        return None
    return model.query.filter(db.func.lower(model.email) == email).first()


def make_token(role, user):
    user_id = getattr(user, {"patient": "patient_id", "caregiver": "caregiver_id", "hcp": "hcp_id"}[role])
    return _serializer().dumps({"role": role, "id": user_id, "fp": _fingerprint(user)})


def user_from_token(token):
    """(role, user) for a valid, unexpired, unused link; otherwise (None, None)."""
    try:
        data = _serializer().loads(token, max_age=RESET_LINK_MINUTES * 60)
    except (SignatureExpired, BadSignature):
        return None, None
    model = ROLE_MODELS.get(data.get("role")) if isinstance(data, dict) else None
    if model is None:
        return None, None
    user = db.session.get(model, data.get("id"))
    if user is None or data.get("fp") != _fingerprint(user) or not can_reset(user):
        return None, None
    return data["role"], user


def reset_email(role, user):
    """(subject, body) of the reset email. The link uses the address the page was opened on."""
    link = url_for("auth.reset_password", token=make_token(role, user), _external=True)
    body = (
        f"Hello {user.name},\n\n"
        f"Someone asked to reset the password for your PD Compass {ROLE_LABELS[role]} account.\n"
        f"To choose a new password, open this link within {RESET_LINK_MINUTES} minutes:\n\n"
        f"{link}\n\n"
        "The link works once. If you didn't ask for this, you can ignore this email: "
        "your password stays the same.\n\n"
        "PD Compass"
    )
    return "Reset your PD Compass password", body
