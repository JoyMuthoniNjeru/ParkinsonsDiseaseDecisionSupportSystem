"""
One-time seed script: creates the initial Admin account (Admins aren't
self-registered -- see FR table) and populates the pre-set catalogs
(SymptomType, MedicationReference) referenced by the ERD notes.

Run with:  python seed.py
"""
from app import create_app
from app.extensions import db
from app.models import Admin, SymptomType, MedicationReference


def strengths(*options):
    """strengths("25/100", 100, "25/250", 250) -> [{"label": "25/100", "dose_amount": 100}, ...]"""
    return [{"label": str(label), "dose_amount": amount} for label, amount in zip(options[::2], options[1::2])]


# PD medications with their pre-fill values, from analysis/medications.py run on PPMI's LEDD log
# (28 Oct 2026): the 20 most used generic medications, their strengths used by >= 10 people
# (most common first) and the median doses per day. Typed in here so seeding never needs the
# PPMI files. Left out: inhaled levodopa (Inbrija), as the app has no "inhaler" form.
# (name, form, unit, times per day, strengths) -- dose_amount is the levodopa part of combo pills.
MEDICATIONS = [
    ("Carbidopa/Levodopa", "tablet", "mg", 3, strengths("25/100", 100, "25/250", 250, "10/100", 100, "50/200", 200)),
    ("Carbidopa/Levodopa CR", "tablet", "mg", 2, strengths("25/100", 100, "50/200", 200)),
    ("Carbidopa/Levodopa ER capsules", "capsule", "mg", 3,
     strengths("48.75/195", 195, "23.75/95", 95, "36.25/145", 145, "61.25/245", 245)),
    ("Carbidopa/Levodopa/Entacapone", "tablet", "mg", 3,
     strengths("25/100/200", 100, "31.25/125/200", 125, "37.5/150/200", 150)),
    ("Levodopa/Benserazide", "tablet", "mg", 3, strengths("100/25", 100, "200/50", 200, "50/12.5", 50)),
    ("Rasagiline", "tablet", "mg", 1, strengths(1, 1, 0.5, 0.5)),
    ("Selegiline", "tablet", "mg", 2, strengths(5, 5)),
    ("Safinamide", "tablet", "mg", 1, strengths(100, 100, 50, 50)),
    ("Amantadine", "tablet", "mg", 2, strengths(100, 100, 137, 137, 68.5, 68.5)),
    ("Entacapone", "tablet", "mg", 4, strengths(200, 200)),
    ("Opicapone", "capsule", "mg", 1, strengths(50, 50)),
    ("Pramipexole", "tablet", "mg", 2, strengths(0.25, 0.25, 0.5, 0.5, 1, 1, 1.5, 1.5)),
    ("Pramipexole ER", "tablet", "mg", 1, strengths(1.05, 1.05, 0.52, 0.52, 1.5, 1.5, 0.26, 0.26)),
    ("Ropinirole", "tablet", "mg", 2, strengths(2, 2, 4, 4, 0.5, 0.5, 1, 1)),
    ("Ropinirole ER", "tablet", "mg", 1, strengths(8, 8, 4, 4, 6, 6, 2, 2)),
    ("Rotigotine patch", "patch", "mg/24h", 1, strengths(6, 6, 4, 4, 2, 2, 8, 8)),
    ("Trihexyphenidyl", "tablet", "mg", 2, strengths(2, 2)),
    ("Piribedil", "tablet", "mg", 2, strengths(50, 50)),
    ("Istradefylline", "tablet", "mg", 1, []),  # no strength used by >= 10 people in PPMI
]
# Old catalog names -> new names, so existing patients' medications are kept
RENAMED = {"Levodopa/Carbidopa": "Carbidopa/Levodopa"}

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

    # Medications: add new ones and update existing ones by name (safe to re-run)
    for old_name, new_name in RENAMED.items():
        old = MedicationReference.query.filter_by(name=old_name).first()
        if old and not MedicationReference.query.filter_by(name=new_name).first():
            old.name = new_name
            print(f"Renamed medication {old_name} -> {new_name}.")
    added = 0
    for name, form, unit, times_per_day, options in MEDICATIONS:
        med = MedicationReference.query.filter_by(name=name).first()
        if not med:
            med = MedicationReference(name=name)
            db.session.add(med)
            added += 1
        med.default_form = form
        med.default_unit = unit
        med.default_times_per_day = times_per_day
        med.strength_options = options
    print(f"Medications: {added} added, {len(MEDICATIONS) - added} updated.")

    db.session.commit()
    print("Seeding complete.")
