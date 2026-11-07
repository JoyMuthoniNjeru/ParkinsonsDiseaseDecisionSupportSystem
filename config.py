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
