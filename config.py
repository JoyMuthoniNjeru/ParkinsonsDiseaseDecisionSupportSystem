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
