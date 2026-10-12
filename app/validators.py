"""
Shared form validation used by the Patient, Caregiver and HCP register routes.
"""

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
