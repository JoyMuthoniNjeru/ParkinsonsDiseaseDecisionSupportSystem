"""
Medications ("prescriptions"), 80% check (12 Nov): only the patient's assigned HCP adds, changes
or stops a patient's medications; the patient (or caregiver) just logs the doses.
"Prescription" here = the medication plan in the app (no pharmacy or e-prescribing).

Changing a medication:
  - instructions / planned end date -> edited in place;
  - dose, unit, strength, form, amount per dose or dose times -> the old prescription stops and a
    new one starts today, so past dose logs keep pointing at the schedule they were taken against.
Stopping = the medication ends yesterday (local), so it's off Today's doses straight away and its
history stays (nothing is deleted).
"""
from datetime import datetime, date, timedelta
from decimal import Decimal, InvalidOperation

from werkzeug.datastructures import MultiDict

from app.extensions import db
from app.models import (
    MedicationReference, PatientMedication, MedicationSchedule, MEDICATION_FORMS, DOSE_UNITS,
)
from app.dashboard_data import local_today

# Times-per-day chips pre-fill the dose time inputs with these (all editable)
DOSE_TIME_PRESETS = {
    1: ["08:00"],
    2: ["08:00", "20:00"],
    3: ["08:00", "13:00", "18:00"],
    4: ["08:00", "12:00", "16:00", "20:00"],
    5: ["07:00", "10:00", "13:00", "16:00", "19:00"],
}

# A change to any of these (or to the dose times) = a new prescription
DOSE_FIELDS = ("dose_amount", "dose_unit", "strength", "form", "quantity_per_dose")


def parse_medication_form(form):
    """Validate the medication form. Returns (error, None) or (None, data)."""
    med = db.session.get(MedicationReference, form.get("medication_id", type=int) or 0)
    if not med:
        return "Please choose a medication from the list.", None
    if form.get("form") not in MEDICATION_FORMS:
        return "Please choose a form (tablet, capsule, patch or liquid).", None
    if form.get("dose_unit") not in DOSE_UNITS:
        return "Please choose a unit (mg, mcg or mg/24h).", None

    try:
        dose_amount = Decimal(form.get("dose_amount", ""))
        quantity = Decimal(form.get("quantity_per_dose", "1"))
    except InvalidOperation:
        return "Dose and amount per dose must be numbers.", None
    if not dose_amount.is_finite() or dose_amount <= 0:
        return "Please enter the dose, e.g. 100.", None
    if not quantity.is_finite() or quantity <= 0 or quantity > 10 or quantity % Decimal("0.5"):
        return "Amount per dose must be in steps of 0.5, e.g. 1 or 1.5.", None

    n = form.get("times_per_day", type=int)
    if n not in DOSE_TIME_PRESETS:
        return "Please choose how many times a day.", None
    try:
        # Only the first n time inputs are shown; the rest are hidden
        times = sorted({datetime.strptime(t, "%H:%M").time() for t in form.getlist("dose_times")[:n]})
    except ValueError:
        return "Please fill in every dose time.", None
    if len(times) != n:
        return "Please give each dose a different time.", None

    try:
        start = date.fromisoformat(form["started_date"]) if form.get("started_date") else local_today()
        end = date.fromisoformat(form["end_date"]) if form.get("end_date") else None
    except ValueError:
        return "Please enter valid dates.", None
    if end and end < start:
        return "The end date can't be before the start date.", None

    return None, {
        "med": {
            "medication_id": med.medication_id,
            "dose_amount": dose_amount,
            "dose_unit": form["dose_unit"],
            "strength": form.get("strength", "").strip() or None,
            "form": form["form"],
            "quantity_per_dose": quantity,
            "started_date": start,
            "end_date": end,
            "instructions": form.get("instructions", "").strip() or None,
        },
        "times": times,
    }


def form_context():
    """What the medication form needs: the PPMI catalog and its pre-fill values."""
    catalog = MedicationReference.query.order_by(MedicationReference.name).all()
    return {"catalog": catalog, "prefill": {m.medication_id: m.prefill() for m in catalog},
            "forms": MEDICATION_FORMS, "units": DOSE_UNITS, "presets": DOSE_TIME_PRESETS,
            "today": local_today().isoformat()}


def form_values(pm):
    """An existing medication as form values (to pre-fill the Change form)."""
    return MultiDict([
        ("medication_id", str(pm.medication_id)),
        ("dose_amount", f"{float(pm.dose_amount):g}" if pm.dose_amount is not None else ""),
        ("dose_unit", pm.dose_unit or ""), ("strength", pm.strength or ""), ("form", pm.form or ""),
        ("quantity_per_dose", f"{float(pm.quantity_per_dose):g}"),
        ("times_per_day", str(len(pm.schedules))),
        ("end_date", pm.end_date.isoformat() if pm.end_date else ""),
        ("instructions", pm.instructions or ""),
    ] + [("dose_times", s.dose_time.strftime("%H:%M")) for s in pm.schedules])


def prescribe(patient, data, hcp):
    """Add a new medication for the patient, prescribed by this HCP."""
    pm = PatientMedication(patient_id=patient.patient_id, prescribed_by_hcp_id=hcp.hcp_id, **data["med"])
    pm.schedules = [MedicationSchedule(dose_time=t) for t in data["times"]]
    db.session.add(pm)
    db.session.commit()
    return pm


def dose_changed(pm, data):
    """True if the dose / amount / form / times differ (= a new prescription is needed)."""
    return (any(getattr(pm, f) != data["med"][f] for f in DOSE_FIELDS)
            or [s.dose_time for s in pm.schedules] != data["times"])


def stop(pm):
    """Stop a medication from today: it ends yesterday (local), so no more doses show; logs stay."""
    pm.end_date = local_today() - timedelta(days=1)
    db.session.commit()


def change(pm, data, hcp):
    """Apply the Change form. Returns (medication now in use, replaced?)."""
    if not dose_changed(pm, data):
        pm.instructions = data["med"]["instructions"]
        pm.end_date = data["med"]["end_date"]
        db.session.commit()
        return pm, False
    # New dose or times: stop the old prescription and start a new one today
    pm.end_date = local_today() - timedelta(days=1)
    data["med"]["started_date"] = local_today()
    new = PatientMedication(patient_id=pm.patient_id, prescribed_by_hcp_id=hcp.hcp_id, **data["med"])
    new.schedules = [MedicationSchedule(dose_time=t) for t in data["times"]]
    db.session.add(new)
    db.session.commit()
    return new, True


def is_current(pm, today=None):
    today = today or local_today()
    return pm.started_date <= today and (pm.end_date is None or pm.end_date >= today)
