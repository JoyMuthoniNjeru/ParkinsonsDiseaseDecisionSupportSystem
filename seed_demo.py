"""
Demo data script: creates synthetic, ready-to-use accounts for every role,
with about two weeks of symptom and medication history, so dashboards are
populated for a demo. All people and data here are fictional.

Run AFTER seed.py (it needs the symptom and medication catalogues):
    python seed_demo.py           # create the demo data (skips if it already exists)
    python seed_demo.py --reset   # delete the demo accounts and recreate them

Every demo account uses the password: Demo1234
"""
import random
import sys
from datetime import date, datetime, timedelta

from app import create_app
from app.extensions import db
from app.models import (
    Patient, Caregiver, HealthcareProfessional, SymptomType, MedicationReference,
    PatientMedication, SymptomRecord, MedicationLog, CaregiverLink,
    CaregiverObservation, Alert,
)
from app.rule_engine import SEVERITY_ALERT_THRESHOLD

DEMO_PASSWORD = "Demo1234"
DAYS_OF_HISTORY = 14

PATIENTS = [
    # Main demo patient: symptoms slowly worsening, some missed doses -> alerts
    {"name": "Amina Wanjiru", "email": "amina@demo.pdmonitor.local", "dob": date(1958, 4, 12)},
    # Second patient: stable, good adherence -> no alerts
    {"name": "Peter Otieno", "email": "peter@demo.pdmonitor.local", "dob": date(1951, 9, 30)},
]
CAREGIVER = {"name": "Grace Wanjiru", "email": "grace@demo.pdmonitor.local"}
HCP_APPROVED = {"name": "Dr. David Kamau", "email": "dr.kamau@demo.pdmonitor.local",
                "institution": "Demo General Hospital"}
HCP_PENDING = {"name": "Dr. Mary Achieng", "email": "dr.achieng@demo.pdmonitor.local",
               "institution": "Demo Neurology Clinic"}

DEMO_EMAILS = [p["email"] for p in PATIENTS] + [
    CAREGIVER["email"], HCP_APPROVED["email"], HCP_PENDING["email"]
]

rng = random.Random(42)  # fixed seed so the demo data is the same every time
now = datetime.utcnow()
day_zero = (now - timedelta(days=DAYS_OF_HISTORY - 1)).replace(hour=0, minute=0, second=0, microsecond=0)


def at(day, hour, minute=0):
    """Timestamp for a given day of the history (0 = oldest) and time of day."""
    return day_zero + timedelta(days=day, hours=hour, minutes=minute)


def clamp(value):
    return max(0, min(10, round(value)))


# ---------------------------------------------------------------------------
# Reset
# ---------------------------------------------------------------------------

def demo_data_exists():
    return Patient.query.filter(Patient.email.in_(DEMO_EMAILS)).first() is not None


def delete_demo_data():
    patients = Patient.query.filter(Patient.email.in_(DEMO_EMAILS)).all()
    caregivers = Caregiver.query.filter(Caregiver.email.in_(DEMO_EMAILS)).all()
    patient_ids = [p.patient_id for p in patients]
    caregiver_ids = [c.caregiver_id for c in caregivers]

    # Links and observations don't cascade from Patient/Caregiver, so remove them first
    CaregiverObservation.query.filter(
        CaregiverObservation.patient_id.in_(patient_ids)
        | CaregiverObservation.caregiver_id.in_(caregiver_ids)
    ).delete(synchronize_session=False)
    CaregiverLink.query.filter(
        CaregiverLink.patient_id.in_(patient_ids) | CaregiverLink.caregiver_id.in_(caregiver_ids)
    ).delete(synchronize_session=False)

    for p in patients:
        db.session.delete(p)  # cascades to symptoms, medications/logs, alerts, insights
    for c in caregivers:
        db.session.delete(c)
    db.session.flush()
    HealthcareProfessional.query.filter(
        HealthcareProfessional.email.in_(DEMO_EMAILS)
    ).delete(synchronize_session=False)
    db.session.commit()
    print("Removed existing demo accounts.")


# ---------------------------------------------------------------------------
# Accounts
# ---------------------------------------------------------------------------

def create_accounts():
    hcp = HealthcareProfessional(credential_status="approved", **HCP_APPROVED)
    hcp.set_password(DEMO_PASSWORD)
    pending_hcp = HealthcareProfessional(credential_status="pending", **HCP_PENDING)
    pending_hcp.set_password(DEMO_PASSWORD)

    caregiver = Caregiver(**CAREGIVER)
    caregiver.set_password(DEMO_PASSWORD)

    patients = []
    for p in PATIENTS:
        patient = Patient(name=p["name"], email=p["email"], date_of_birth=p["dob"], hcp=hcp)
        patient.set_password(DEMO_PASSWORD)
        patients.append(patient)

    db.session.add_all([hcp, pending_hcp, caregiver, *patients])
    db.session.flush()  # assigns the primary keys
    return patients, caregiver


def create_links(amina, peter, caregiver):
    db.session.add_all([
        # Approved: Grace can see Amina's dashboard and log observations
        CaregiverLink(patient_id=amina.patient_id, caregiver_id=caregiver.caregiver_id,
                      status="approved", linked_date=at(0, 10)),
        # Pending: shows "You have 1 pending link request" on Grace's dashboard
        CaregiverLink(patient_id=peter.patient_id, caregiver_id=caregiver.caregiver_id,
                      status="pending", linked_date=at(DAYS_OF_HISTORY - 2, 15)),
    ])


# ---------------------------------------------------------------------------
# Symptom history
# ---------------------------------------------------------------------------

def symptom_types():
    return {s.name: s for s in SymptomType.query.all()}


def add_symptoms(patient, plan, notes):
    """plan: {symptom name: (starting severity, daily drift, every N days, hour)}"""
    types = symptom_types()
    records = []
    for day in range(DAYS_OF_HISTORY):
        for name, (start, drift, every, hour) in plan.items():
            if day % every:
                continue
            ts = at(day, hour, rng.randint(0, 45))
            if ts > now:
                continue
            severity = clamp(start + drift * day + rng.uniform(-1, 1))
            records.append(SymptomRecord(
                patient_id=patient.patient_id,
                symptom_type_id=types[name].symptom_type_id,
                severity=severity,
                timestamp=ts,
                notes=notes.get((day, name)),
            ))
    db.session.add_all(records)
    return records


# ---------------------------------------------------------------------------
# Medication history
# ---------------------------------------------------------------------------

def add_medication(patient, med_name, hours, missed_days, skip_days=()):
    med = MedicationReference.query.filter_by(name=med_name).first()
    pm = PatientMedication(patient_id=patient.patient_id, medication_id=med.medication_id,
                           started_date=day_zero.date())
    db.session.add(pm)
    db.session.flush()

    logs = []
    for day in range(DAYS_OF_HISTORY):
        if day in skip_days:
            continue
        for hour in hours:
            ts = at(day, hour, rng.randint(0, 20))
            if ts > now:
                continue
            status = "missed" if (day, hour) in missed_days else "taken"
            logs.append(MedicationLog(patient_medication_id=pm.patient_medication_id,
                                      adherence_status=status, timestamp=ts))
    db.session.add_all(logs)
    return pm, logs


# ---------------------------------------------------------------------------
# Alerts: produced with the same rules and wording as app/rule_engine.py,
# replayed in time order as if each entry had been logged live.
# ---------------------------------------------------------------------------

def add_symptom_alerts(patient, records):
    for r in records:
        if r.severity >= SEVERITY_ALERT_THRESHOLD:
            db.session.add(Alert(
                patient_id=patient.patient_id,
                rule_triggered=f"High severity symptom reported ({r.severity}/10)",
                timestamp=r.timestamp,
            ))


def add_missed_dose_alerts(patient, med_name, logs):
    for log in logs:
        if log.adherence_status == "missed":
            db.session.add(Alert(
                patient_id=patient.patient_id,
                rule_triggered=f"Missed dose: {med_name}",
                timestamp=log.timestamp,
            ))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    if SymptomType.query.count() == 0 or MedicationReference.query.count() == 0:
        print("Catalogues are empty. Run `python seed.py` first.")
        return

    if "--reset" in sys.argv:
        delete_demo_data()
    elif demo_data_exists():
        print("Demo data already exists. Use `python seed_demo.py --reset` to recreate it.")
        return

    (amina, peter), caregiver = create_accounts()
    create_links(amina, peter, caregiver)
    last = DAYS_OF_HISTORY - 1

    # Amina: motor symptoms slowly worsening over the two weeks
    amina_records = add_symptoms(amina, {
        "Tremor":            (4.5, 0.20, 1, 9),
        "Rigidity":          (3.5, 0.15, 1, 9),
        "Fatigue":           (4.0, 0.10, 2, 19),
        "Sleep disturbance": (3.0, 0.15, 3, 7),
    }, notes={
        (2, "Tremor"): "Worse in the right hand this morning.",
        (6, "Fatigue"): "Tired after a short walk.",
        (10, "Tremor"): "Hard to hold a cup steady.",
        (last - 1, "Rigidity"): "Stiff getting out of bed.",
        (last, "Tremor"): "Shaking most of the morning.",
    })
    _, amina_levodopa_logs = add_medication(amina, "Levodopa/Carbidopa", hours=(8, 20),
                                            missed_days={(3, 20)})
    # Pramipexole: three missed doses in the last week (one alert each)
    _, amina_pramipexole_logs = add_medication(amina, "Pramipexole", hours=(9,),
                                               missed_days={(last - 6, 9), (last - 3, 9), (last - 1, 9)},
                                               skip_days={last})

    # Peter: stable, mild symptoms, every dose taken
    peter_records = add_symptoms(peter, {
        "Tremor":  (3.0, 0.0, 2, 10),
        "Fatigue": (2.5, 0.0, 2, 18),
    }, notes={(4, "Tremor"): "Feeling good this week."})
    add_medication(peter, "Levodopa/Carbidopa", hours=(8, 20), missed_days=set())

    add_symptom_alerts(amina, amina_records)
    add_symptom_alerts(peter, peter_records)
    add_missed_dose_alerts(amina, "Levodopa/Carbidopa", amina_levodopa_logs)
    add_missed_dose_alerts(amina, "Pramipexole", amina_pramipexole_logs)

    for day, hour, note in [
        (3, 18, "Amina seemed more tired than usual and napped twice."),
        (8, 12, "Noticed a slight shuffle when walking to the shops."),
        (last - 1, 17, "Forgot her afternoon tablet again; set a phone reminder."),
    ]:
        db.session.add(CaregiverObservation(caregiver_id=caregiver.caregiver_id,
                                            patient_id=amina.patient_id,
                                            note=note, timestamp=at(day, hour)))

    db.session.commit()

    alerts = Alert.query.filter(Alert.patient_id.in_([amina.patient_id, peter.patient_id])).count()
    print(f"Created demo data: {len(amina_records) + len(peter_records)} symptom entries, "
          f"{alerts} alerts.")
    print(f"\nDemo logins (password for all: {DEMO_PASSWORD})")
    print(f"  Patient     {PATIENTS[0]['email']}  (worsening trend, alerts, linked caregiver)")
    print(f"  Patient     {PATIENTS[1]['email']}  (stable, pending caregiver request)")
    print(f"  Caregiver   {CAREGIVER['email']}")
    print(f"  HCP         {HCP_APPROVED['email']}  (approved, both patients assigned)")
    print(f"  HCP         {HCP_PENDING['email']}  (pending: approve it as Admin)")
    print("  Admin       admin@pdmonitor.local / changeme123 (from seed.py)")


if __name__ == "__main__":
    app = create_app()
    with app.app_context():
        main()
