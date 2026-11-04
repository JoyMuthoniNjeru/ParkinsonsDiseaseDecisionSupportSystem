"""
Shared pytest setup.

Tests run against their OWN PostgreSQL database (TEST_DATABASE_URL in .env), never pd_dss:
every test drops and recreates all tables, so it starts from a blank slate. As a safety net,
the tests refuse to run unless that database's name ends in "_test".

Tests never read PPMI_DATA_DIR (DUA): PPMI_DATA_DIR is switched off here, and ML tests that need
a baseline write a tiny made-up CSV into pytest's tmp_path.

Run:  pytest            (or  pytest --cov=app  for coverage)
"""
import os
from datetime import date, datetime, time, timedelta

import pytest
from dotenv import load_dotenv
from sqlalchemy.engine import make_url

from config import Config

load_dotenv()
TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
if not TEST_DATABASE_URL or not (make_url(TEST_DATABASE_URL).database or "").endswith("_test"):
    pytest.exit("Set TEST_DATABASE_URL in .env to a database whose name ends in _test "
                "(e.g. .../pd_dss_test). Tests drop all tables, so they never run on pd_dss.",
                returncode=1)

from app import create_app          # noqa: E402  (after the safety check)
from app.extensions import db       # noqa: E402
from app.models import (            # noqa: E402
    Patient, Caregiver, HealthcareProfessional, Admin, CaregiverLink, SymptomType,
    MedicationReference, PatientMedication, MedicationSchedule, MedicationLog, SymptomRecord,
)

PASSWORD = "Passw0rd1"
UTC_OFFSET = timedelta(hours=Config.DISPLAY_UTC_OFFSET_HOURS)


class TestingConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = TEST_DATABASE_URL
    SECRET_KEY = "test-only"
    PPMI_DATA_DIR = None   # never touch the real PPMI data from tests


# ---------------------------------------------------------------------------
# App + database: fresh tables for every test
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def _app():
    """One app + one set of tables for the whole test run (creating tables is slow)."""
    app = create_app(TestingConfig)
    with app.app_context():
        db.drop_all()
        db.create_all()
    yield app
    with app.app_context():
        db.session.remove()
        db.drop_all()


@pytest.fixture
def app(_app, monkeypatch):
    """Each test gets the app with EMPTY tables (rows deleted afterwards), so tests don't
    affect each other."""
    # The app hashes passwords with slow scrypt (right for real users); test users get a fast hash
    import app.models as models
    monkeypatch.setattr(models, "generate_password_hash",
                        lambda raw: _fast_hash(raw))
    with _app.app_context():
        yield _app
        db.session.rollback()
        for table in reversed(db.metadata.sorted_tables):   # children before parents (foreign keys)
            db.session.execute(table.delete())
        db.session.commit()
        db.session.remove()


def _fast_hash(raw):
    from werkzeug.security import generate_password_hash
    return generate_password_hash(raw, method="pbkdf2:sha256:1000")


@pytest.fixture
def client(app):
    return app.test_client()


def login(client, role, email, password=PASSWORD):
    """Log a test client in through the real login form."""
    return client.post("/auth/login", data={"role": role, "email": email, "password": password})


# ---------------------------------------------------------------------------
# People
# ---------------------------------------------------------------------------

def _add(obj, password=PASSWORD):
    obj.set_password(password)
    db.session.add(obj)
    db.session.commit()
    return obj


@pytest.fixture
def hcp(app):
    return _add(HealthcareProfessional(name="Dr Test", email="hcp@test.local",
                                       institution="Test Hospital", credential_status="approved"))


@pytest.fixture
def patient(app, hcp):
    """The main test patient: diagnosed 4 years ago (3-5 years stage), assigned to `hcp`."""
    return _add(Patient(name="Pat Test", email="patient@test.local", date_of_birth=date(1955, 6, 1),
                        diagnosis_year=date.today().year - 4, hcp_id=hcp.hcp_id))


@pytest.fixture
def other_patient(app):
    """A second patient, for checking one patient can't touch another's data."""
    return _add(Patient(name="Other Test", email="other@test.local", date_of_birth=date(1960, 1, 1)))


@pytest.fixture
def caregiver(app, patient):
    """A caregiver with an APPROVED link to `patient`."""
    cg = _add(Caregiver(name="Carer Test", email="carer@test.local"))
    db.session.add(CaregiverLink(patient_id=patient.patient_id, caregiver_id=cg.caregiver_id, status="approved"))
    db.session.commit()
    return cg


@pytest.fixture
def admin(app):
    return _add(Admin(name="Admin Test", email="admin@test.local"))


# ---------------------------------------------------------------------------
# Catalogs, medication and logs
# ---------------------------------------------------------------------------

@pytest.fixture
def tremor(app):
    st = SymptomType(name="Tremor", category="motor", ppmi_code="NP2TRMR", item_number="2.10")
    db.session.add(st)
    db.session.commit()
    return st


@pytest.fixture
def medication(app, patient):
    """Pramipexole 0.25 mg, 1 tablet at 08:00 / 13:00 / 18:00 (local), started 30 days ago."""
    pm = PatientMedication(patient_id=patient.patient_id,
                           medication=MedicationReference(name="Pramipexole"),
                           dose_amount=0.25, dose_unit="mg", form="tablet", quantity_per_dose=1,
                           started_date=date.today() - timedelta(days=30))
    pm.schedules = [MedicationSchedule(dose_time=time(h)) for h in (8, 13, 18)]
    db.session.add(pm)
    db.session.commit()
    return pm


def local_to_utc(day, clock):
    """A local date + clock time -> the naive UTC datetime the app stores."""
    return datetime.combine(day, clock) - UTC_OFFSET


def local_today():
    return (datetime.utcnow() + UTC_OFFSET).date()


def make_log(pm, status, days_ago=1, hour=13, logged_after=timedelta(hours=1)):
    """A dose log for pm's `hour` schedule, `days_ago` local days back. Missed doses are
    'logged' an hour after the scheduled time (that's the time the pattern rule counts)."""
    schedule = next(s for s in pm.schedules if s.dose_time.hour == hour)
    scheduled = local_to_utc(local_today() - timedelta(days=days_ago), schedule.dose_time)
    log = MedicationLog(patient_medication_id=pm.patient_medication_id, schedule_id=schedule.schedule_id,
                        status=status, scheduled_time=scheduled, timestamp=scheduled + logged_after)
    if status in ("taken", "late"):
        log.taken_at = scheduled + (timedelta(minutes=10) if status == "taken" else timedelta(hours=1))
        log.quantity_taken = 1
    db.session.add(log)
    db.session.commit()
    return log


def make_symptom(patient, symptom_type, severity, when=None):
    record = SymptomRecord(patient_id=patient.patient_id, symptom_type_id=symptom_type.symptom_type_id,
                           severity=severity, timestamp=when or datetime.utcnow())
    db.session.add(record)
    db.session.commit()
    return record
