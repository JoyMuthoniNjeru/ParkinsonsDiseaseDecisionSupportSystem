"""
One-time seed script: creates the initial Admin account (Admins aren't
self-registered -- see FR table) and populates the pre-set catalogs
(SymptomType, MedicationReference) referenced by the ERD notes.

Run with:  python seed.py
"""
from app import create_app
from app.extensions import db
from app.models import Admin, SymptomType, MedicationReference

app = create_app()

with app.app_context():
    if not Admin.query.filter_by(email="admin@pdmonitor.local").first():
        admin = Admin(name="System Admin", email="admin@pdmonitor.local")
        admin.set_password("changeme123")
        db.session.add(admin)
        print("Created admin@pdmonitor.local / changeme123 -- change this password.")

    if SymptomType.query.count() == 0:
        symptoms = [
            ("Tremor", "motor"), ("Rigidity", "motor"), ("Bradykinesia", "motor"),
            ("Postural instability", "motor"), ("Sleep disturbance", "non-motor"),
            ("Depression/anxiety", "non-motor"), ("Fatigue", "non-motor"),
            ("Constipation", "non-motor"),
        ]
        for name, category in symptoms:
            db.session.add(SymptomType(name=name, category=category))
        print(f"Seeded {len(symptoms)} symptom types.")

    if MedicationReference.query.count() == 0:
        meds = ["Levodopa/Carbidopa", "Pramipexole", "Ropinirole", "Rasagiline",
                "Amantadine", "Entacapone"]
        for name in meds:
            db.session.add(MedicationReference(name=name))
        print(f"Seeded {len(meds)} medications.")

    db.session.commit()
    print("Seeding complete.")
