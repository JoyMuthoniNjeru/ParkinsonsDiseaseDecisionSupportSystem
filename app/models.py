"""
SQLAlchemy models -- direct translation of the Chapter 4 class diagram / DB schema.
Concrete-table inheritance: Patient, Caregiver, HealthcareProfessional, Admin each
have their own table (no shared "users" table), matching the DB Schema diagram.
"""
from datetime import datetime, date
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import UserMixin
from sqlalchemy.orm import validates
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
    # Optional. Lets the ML component compare the patient with PPMI people at the same stage
    # (0-2 / 3-5 / 6+ years since diagnosis). Without it, that comparison is skipped.
    diagnosis_year = db.Column(db.Integer, nullable=True)
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

    def years_since_diagnosis(self):
        """Whole years since the diagnosis year (e.g. 4), or None if it isn't known."""
        return date.today().year - self.diagnosis_year if self.diagnosis_year else None

    def diagnosis_year_error(self, year):
        """Why `year` can't be this patient's diagnosis year, or None if it's fine."""
        if year is None or not 1900 <= year <= date.today().year:
            return f"Year of diagnosis must be a year up to {date.today().year}."
        if year < self.date_of_birth.year:
            return "Year of diagnosis can't be before the year of birth."
        return None


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
    # The MDS-UPDRS item this symptom is (see analysis/updrs_items.py). The ML component uses
    # ppmi_code to compare a patient's scores with the PPMI baseline. Old pre-PPMI symptom
    # types have no code: they're kept for existing entries but hidden from the log forms.
    ppmi_code = db.Column(db.String(10), nullable=True, unique=True)  # e.g. NP2TRMR
    item_number = db.Column(db.String(5), nullable=True)              # e.g. "2.10"

    @property
    def sort_key(self):
        """'1.10' sorts after '1.9' (number order, not text order)."""
        return tuple(int(part) for part in self.item_number.split(".")) if self.item_number else (99,)

    @classmethod
    def loggable(cls):
        """The 26 MDS-UPDRS items patients can log, in item order (1.1 ... 2.13)."""
        return sorted(cls.query.filter(cls.ppmi_code.isnot(None)).all(), key=lambda t: t.sort_key)


class MedicationReference(db.Model):
    __tablename__ = "medication_reference"

    medication_id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)

    # Pre-fill values for the Add medication page, from PPMI's LEDD log (see
    # analysis/medications.py and seed.py). All optional, and the patient can change them.
    default_form = db.Column(db.String(20), nullable=True)       # tablet / capsule / patch / liquid
    default_unit = db.Column(db.String(10), nullable=True)       # mg / mcg / mg/24h
    default_times_per_day = db.Column(db.Integer, nullable=True)
    # Usual strengths, most common first, e.g. [{"label": "25/100", "dose_amount": 100}, ...]
    # dose_amount is the levodopa part for combination pills.
    strength_options = db.Column(db.JSON, nullable=True)

    def prefill(self):
        """What the Add medication page fills in when this medication is picked."""
        return {
            "form": self.default_form,
            "unit": self.default_unit,
            "times_per_day": self.default_times_per_day,
            "strengths": self.strength_options or [],
        }


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


# Symptom severity: the MDS-UPDRS 0-4 scale, same as PPMI Part I/II items, so patient data
# and the ML baseline compare directly. The ONE place the labels live -- use these everywhere.
SEVERITY_LABELS = ["Normal", "Slight", "Mild", "Moderate", "Severe"]  # index = score
SEVERITY_MAX = len(SEVERITY_LABELS) - 1  # 4


class SymptomRecord(db.Model):
    __tablename__ = "symptom_record"

    record_id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey("patient.patient_id"), nullable=False)
    symptom_type_id = db.Column(db.Integer, db.ForeignKey("symptom_type.symptom_type_id"), nullable=False)
    severity = db.Column(db.Integer, nullable=False)  # 0-4, MDS-UPDRS scale (see SEVERITY_LABELS)
    timestamp = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    notes = db.Column(db.Text, nullable=True)
    # Edit trace (patient can edit an entry on the same day): the score before the FIRST edit,
    # and when it was last edited (UTC). Both null = never edited.
    original_severity = db.Column(db.Integer, nullable=True)
    edited_at = db.Column(db.DateTime, nullable=True)

    symptom_type = db.relationship("SymptomType")

    @validates("severity")
    def _check_severity(self, key, value):
        if value not in range(len(SEVERITY_LABELS)):
            raise ValueError(f"Severity must be 0-{SEVERITY_MAX}, got {value!r}")
        return value

    def severity_label(self):
        """e.g. "Mild"."""
        return SEVERITY_LABELS[self.severity] if 0 <= self.severity <= SEVERITY_MAX else "Unknown"

    def severity_text(self):
        """e.g. "2 / 4 · Mild" -- how scores are shown on every dashboard."""
        return f"{self.severity} / {SEVERITY_MAX} · {self.severity_label()}"


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
    # Correction trace (patient can correct a dose within 3 days): the status before the FIRST
    # correction, and when it was last corrected (UTC). Both null = never corrected.
    original_status = db.Column(db.String(10), nullable=True)
    corrected_at = db.Column(db.DateTime, nullable=True)

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
    # Which dose log / symptom entry raised this alert, so a correction or edit (or Undo) can
    # find and remove exactly its alerts. Null for old alerts made before these columns existed.
    medication_log_id = db.Column(
        db.Integer, db.ForeignKey("medication_log.log_id", ondelete="SET NULL"), nullable=True
    )
    symptom_record_id = db.Column(
        db.Integer, db.ForeignKey("symptom_record.record_id", ondelete="SET NULL"), nullable=True
    )


class Insight(db.Model):
    __tablename__ = "insight"

    insight_id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey("patient.patient_id"), nullable=False)
    content = db.Column(db.Text, nullable=False)
    generated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
