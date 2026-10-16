"""
SQLAlchemy models -- direct translation of the Chapter 4 class diagram / DB schema.
Concrete-table inheritance: Patient, Caregiver, HealthcareProfessional, Admin each
have their own table (no shared "users" table), matching the DB Schema diagram.
"""
from datetime import datetime, date
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import UserMixin
from app.extensions import db


# ---------------------------------------------------------------------------
# Human actors
# ---------------------------------------------------------------------------

class Patient(UserMixin, db.Model):
    __tablename__ = "patient"

    patient_id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(150), nullable=False, unique=True)
    password_hash = db.Column(db.String(255), nullable=False)
    date_of_birth = db.Column(db.Date, nullable=False)
    account_status = db.Column(db.String(20), nullable=False, default="active")  # active/deactivated
    hcp_id = db.Column(db.Integer, db.ForeignKey("healthcare_professional.hcp_id"), nullable=True)

    hcp = db.relationship("HealthcareProfessional", backref="patients")
    symptom_records = db.relationship("SymptomRecord", backref="patient", cascade="all, delete-orphan")
    medications = db.relationship("PatientMedication", backref="patient", cascade="all, delete-orphan")
    alerts = db.relationship("Alert", backref="patient", cascade="all, delete-orphan")
    insights = db.relationship("Insight", backref="patient", cascade="all, delete-orphan")

    def set_password(self, raw):
        self.password_hash = generate_password_hash(raw)

    def check_password(self, raw):
        return check_password_hash(self.password_hash, raw)

    def get_id(self):
        return f"patient:{self.patient_id}"


class Caregiver(UserMixin, db.Model):
    __tablename__ = "caregiver"

    caregiver_id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(150), nullable=False, unique=True)
    password_hash = db.Column(db.String(255), nullable=False)
    account_status = db.Column(db.String(20), nullable=False, default="active")

    def set_password(self, raw):
        self.password_hash = generate_password_hash(raw)

    def check_password(self, raw):
        return check_password_hash(self.password_hash, raw)

    def get_id(self):
        return f"caregiver:{self.caregiver_id}"


class HealthcareProfessional(UserMixin, db.Model):
    __tablename__ = "healthcare_professional"

    hcp_id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(150), nullable=False, unique=True)
    password_hash = db.Column(db.String(255), nullable=False)
    institution = db.Column(db.String(150), nullable=False)
    credential_status = db.Column(db.String(20), nullable=False, default="pending")  # pending/approved/rejected

    def set_password(self, raw):
        self.password_hash = generate_password_hash(raw)

    def check_password(self, raw):
        return check_password_hash(self.password_hash, raw)

    def get_id(self):
        return f"hcp:{self.hcp_id}"

    def is_active_account(self):
        return self.credential_status == "approved"


class Admin(UserMixin, db.Model):
    __tablename__ = "admin"

    admin_id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(150), nullable=False, unique=True)
    password_hash = db.Column(db.String(255), nullable=False)

    def set_password(self, raw):
        self.password_hash = generate_password_hash(raw)

    def check_password(self, raw):
        return check_password_hash(self.password_hash, raw)

    def get_id(self):
        return f"admin:{self.admin_id}"


# ---------------------------------------------------------------------------
# Catalogs (pre-seeded reference data -- see ERD notes)
# ---------------------------------------------------------------------------

class SymptomType(db.Model):
    __tablename__ = "symptom_type"

    symptom_type_id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False)
    category = db.Column(db.String(20), nullable=False)  # motor / non-motor


class MedicationReference(db.Model):
    __tablename__ = "medication_reference"

    medication_id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)


# ---------------------------------------------------------------------------
# Patient's personal medication list + logging
# ---------------------------------------------------------------------------

MEDICATION_FORMS = ["tablet", "capsule", "patch", "liquid"]
DOSE_UNITS = ["mg", "mcg", "mg/24h"]

# How each form is counted, e.g. "1.5 tablets" -- (singular, plural)
_FORM_WORDS = {"tablet": ("tablet", "tablets"), "capsule": ("capsule", "capsules"),
               "patch": ("patch", "patches"), "liquid": ("dose", "doses")}


class PatientMedication(db.Model):
    __tablename__ = "patient_medication"

    patient_medication_id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey("patient.patient_id"), nullable=False)
    medication_id = db.Column(db.Integer, db.ForeignKey("medication_reference.medication_id"), nullable=False)
    started_date = db.Column(db.Date, nullable=False, default=date.today)
    end_date = db.Column(db.Date, nullable=True)  # null = ongoing

    # Dose details (pointcheck redesign) -- nullable so older rows still load
    dose_amount = db.Column(db.Numeric(8, 2), nullable=True)     # e.g. 100
    dose_unit = db.Column(db.String(10), nullable=True)          # mg / mcg / mg/24h
    strength = db.Column(db.String(30), nullable=True)           # e.g. "25/100" for carbidopa/levodopa
    form = db.Column(db.String(20), nullable=True)               # tablet / capsule / patch / liquid
    quantity_per_dose = db.Column(db.Numeric(4, 1), nullable=False, default=1,
                                  server_default="1")  # 0.5 steps, e.g. 1.5
    instructions = db.Column(db.Text, nullable=True)             # e.g. "Take with food"

    medication = db.relationship("MedicationReference")
    logs = db.relationship("MedicationLog", backref="patient_medication", cascade="all, delete-orphan")
    schedules = db.relationship("MedicationSchedule", backref="patient_medication",
                                cascade="all, delete-orphan", order_by="MedicationSchedule.dose_time")

    def dose_label(self):
        """e.g. "Levodopa/Carbidopa 25/100 mg" or "Pramipexole 0.25 mg"."""
        amount = self.strength or (f"{float(self.dose_amount):g}" if self.dose_amount is not None else "")
        return " ".join(part for part in (self.medication.name, amount, self.dose_unit) if part)

    def quantity_label(self):
        """e.g. "1.5 tablets" or "1 patch"."""
        q = float(self.quantity_per_dose)
        singular, plural = _FORM_WORDS.get(self.form, ("dose", "doses"))
        return f"{q:g} {singular if q == 1 else plural}"

    def times_label(self):
        """e.g. "08:00, 13:00, 18:00"."""
        return ", ".join(s.dose_time.strftime("%H:%M") for s in self.schedules)


class MedicationSchedule(db.Model):
    """One row per daily dose time, e.g. 3x a day = 3 rows."""
    __tablename__ = "medication_schedule"

    schedule_id = db.Column(db.Integer, primary_key=True)
    patient_medication_id = db.Column(
        db.Integer, db.ForeignKey("patient_medication.patient_medication_id"), nullable=False
    )
    dose_time = db.Column(db.Time, nullable=False)  # local clock time, e.g. 08:00


class SymptomRecord(db.Model):
    __tablename__ = "symptom_record"

    record_id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey("patient.patient_id"), nullable=False)
    symptom_type_id = db.Column(db.Integer, db.ForeignKey("symptom_type.symptom_type_id"), nullable=False)
    severity = db.Column(db.Integer, nullable=False)  # 0-10 scale (changing to 0-4 in the symptom-scale step)
    timestamp = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    notes = db.Column(db.Text, nullable=True)

    symptom_type = db.relationship("SymptomType")


class MedicationLog(db.Model):
    __tablename__ = "medication_log"

    log_id = db.Column(db.Integer, primary_key=True)
    patient_medication_id = db.Column(
        db.Integer, db.ForeignKey("patient_medication.patient_medication_id"), nullable=False
    )
    schedule_id = db.Column(
        db.Integer, db.ForeignKey("medication_schedule.schedule_id", ondelete="SET NULL"), nullable=True
    )  # null = extra / unscheduled dose
    status = db.Column(db.String(10), nullable=False)  # taken / missed / late / skipped
    scheduled_time = db.Column(db.DateTime, nullable=True)  # UTC, null for extra doses
    taken_at = db.Column(db.DateTime, nullable=True)        # UTC, null if missed/skipped
    quantity_taken = db.Column(db.Numeric(4, 1), nullable=True)
    reason = db.Column(db.String(100), nullable=True)       # e.g. "forgot", "side effects"
    note = db.Column(db.Text, nullable=True)
    timestamp = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)  # when the log was made

    schedule = db.relationship("MedicationSchedule", backref="logs")


# ---------------------------------------------------------------------------
# Caregiver linking + observations
# ---------------------------------------------------------------------------

class CaregiverLink(db.Model):
    __tablename__ = "caregiver_link"

    link_id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey("patient.patient_id"), nullable=False)
    caregiver_id = db.Column(db.Integer, db.ForeignKey("caregiver.caregiver_id"), nullable=False)
    status = db.Column(db.String(20), nullable=False, default="pending")  # pending/approved/declined
    linked_date = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    patient = db.relationship("Patient", backref="caregiver_links")
    caregiver = db.relationship("Caregiver", backref="caregiver_links")


class CaregiverObservation(db.Model):
    __tablename__ = "caregiver_observation"

    observation_id = db.Column(db.Integer, primary_key=True)
    caregiver_id = db.Column(db.Integer, db.ForeignKey("caregiver.caregiver_id"), nullable=False)
    patient_id = db.Column(db.Integer, db.ForeignKey("patient.patient_id"), nullable=False)
    note = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    caregiver = db.relationship("Caregiver", backref="observations")
    patient = db.relationship("Patient", backref="caregiver_observations")


# ---------------------------------------------------------------------------
# System-actor outputs
# ---------------------------------------------------------------------------

class Alert(db.Model):
    __tablename__ = "alert"

    alert_id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey("patient.patient_id"), nullable=False)
    rule_triggered = db.Column(db.String(120), nullable=False)
    timestamp = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


class Insight(db.Model):
    __tablename__ = "insight"

    insight_id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey("patient.patient_id"), nullable=False)
    content = db.Column(db.Text, nullable=False)
    generated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
