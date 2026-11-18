import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-change-me")
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/pd_dss"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    # Timestamps are stored in UTC; templates show them in local time.
    # Kenya is UTC+3 all year (no daylight saving).
    DISPLAY_UTC_OFFSET_HOURS = 3
    # PPMI data lives outside the repo (DUA). The ML component only reads the small
    # aggregated baseline tables in PPMI_DATA_DIR/output, made by the analysis/ scripts.
    PPMI_DATA_DIR = os.environ.get("PPMI_DATA_DIR")
    # CSRF tokens last for the whole login session instead of 1 hour, so a slow patient who
    # leaves the Log symptoms page open doesn't lose their entries.
    WTF_CSRF_TIME_LIMIT = None
    # Medication photos (80% check): uploaded by the HCP, stored OUTSIDE static/ in
    # instance/uploads/meds (git-ignored) and served only to people allowed to see that patient.
    # None = the default folder (see app/photos.py). Requests bigger than this are refused (413).
    MED_PHOTO_DIR = None
    MAX_CONTENT_LENGTH = 5 * 1024 * 1024   # 5 MB (phone photos are often 3-5 MB; they're shrunk on save)
    # Email (password reset links, app/mail.py). No MAIL_SERVER = "console mode": emails are
    # printed in the terminal instead of sent (works offline). Gmail: smtp.gmail.com, port 587,
    # MAIL_PASSWORD = a Google *App password*.
    MAIL_SERVER = os.environ.get("MAIL_SERVER")
    MAIL_PORT = int(os.environ.get("MAIL_PORT", "587"))
    MAIL_USERNAME = os.environ.get("MAIL_USERNAME")
    MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD")
    MAIL_SENDER = os.environ.get("MAIL_SENDER")   # optional; defaults to MAIL_USERNAME
