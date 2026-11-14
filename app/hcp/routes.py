from flask import render_template, request, redirect, url_for, flash, abort
from flask_login import login_required, current_user
from app.hcp import bp
from app.extensions import db
from app.validators import password_error, parse_patient_emails
from app.models import HealthcareProfessional, Patient, PatientMedication, CaregiverObservation, MedicationReference
from app import medications
from app.photos import save_photo, copy_catalog_photo
from app.dashboard_data import todays_doses, patient_stats, patient_overview
from app.ml_component import analyze_patient
from app.patient.routes import insights_message


def _require_hcp():
    if not isinstance(current_user, HealthcareProfessional):
        flash("Please log in as a healthcare professional.", "error")
        return redirect(url_for("auth.login"))
    return None


@bp.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        existing = HealthcareProfessional.query.filter_by(email=request.form["email"]).first()
        if existing:
            flash("An account with that email already exists.", "error")
            return redirect(url_for("hcp.register"))

        error = password_error(request.form["password"], request.form["confirm_password"])
        if error:
            flash(error, "error")
            return redirect(url_for("hcp.register"))
        # Optional list of their patients' emails; whether each has an account is only shown to
        # the Admin (never to the applicant), and approval assigns them (80% check)
        error, patient_emails = parse_patient_emails(request.form.get("patient_emails"))
        if error:
            flash(error, "error")
            return redirect(url_for("hcp.register"))

        hcp = HealthcareProfessional(
            name=request.form["name"],
            email=request.form["email"],
            institution=request.form["institution"],
            credential_status="pending",
            requested_patient_emails="\n".join(patient_emails) or None,
        )
        hcp.set_password(request.form["password"])
        db.session.add(hcp)
        db.session.commit()
        flash("Application submitted. An Admin must approve your account before you can log in.", "success")
        return redirect(url_for("auth.login"))

    return render_template("hcp/register.html")


@bp.route("/dashboard")
@login_required
def dashboard():
    guard = _require_hcp()
    if guard:
        return guard

    patients = Patient.query.filter_by(hcp_id=current_user.hcp_id).order_by(Patient.name).all()
    summaries = {p.patient_id: patient_stats(p.patient_id, todays_doses(p.patient_id)) for p in patients}
    return render_template("hcp/dashboard.html", patients=patients, summaries=summaries)


@bp.route("/patient/<int:patient_id>")
@login_required
def patient_dashboard(patient_id):
    guard = _require_hcp()
    if guard:
        return guard

    patient = Patient.query.filter_by(patient_id=patient_id, hcp_id=current_user.hcp_id).first_or_404()
    observations = (
        CaregiverObservation.query.filter_by(patient_id=patient_id)
        .order_by(CaregiverObservation.timestamp.desc()).limit(10).all()
    )
    return render_template("hcp/patient_dashboard.html", patient=patient,
                           **patient_overview(patient_id), observations=observations)


# ---------------------------------------------------------------------------
# Medications ("prescriptions", 80% check): only the patient's ASSIGNED HCP adds, changes or
# stops them (another HCP -> 404). Logic in app/medications.py.
# ---------------------------------------------------------------------------

def _assigned_patient(patient_id):
    return Patient.query.filter_by(patient_id=patient_id, hcp_id=current_user.hcp_id).first_or_404()


def _uploaded_photo():
    """The optional photo from the form: (None, None) if none chosen, else photos.save_photo()."""
    file = request.files.get("photo")
    if not file or not file.filename:
        return None, None
    return save_photo(file)


def _patients_medication(patient, pm_id):
    pm = db.session.get(PatientMedication, pm_id)
    if not pm or pm.patient_id != patient.patient_id:
        abort(404)
    return pm


@bp.route("/patient/<int:patient_id>/medications/add", methods=["GET", "POST"])
@login_required
def add_medication(patient_id):
    guard = _require_hcp()
    if guard:
        return guard
    patient = _assigned_patient(patient_id)
    if request.method == "POST":
        error, data = medications.parse_medication_form(request.form)
        photo = None
        if not error:
            error, photo = _uploaded_photo()          # checked after the form, so no orphan files
        if error:
            flash(error, "error")
        else:
            if photo is None and not request.form.get("no_photo"):
                # start from the medication's standard photo (its own copy; editable on Change)
                photo = copy_catalog_photo(db.session.get(MedicationReference, data["med"]["medication_id"]))
            pm = medications.prescribe(patient, data, current_user, photo_filename=photo)
            flash(f"Prescribed {pm.dose_label()}, {len(data['times'])}x a day, for {patient.name}.", "success")
            return redirect(url_for("hcp.patient_dashboard", patient_id=patient_id))
    # request.form is empty on GET, and holds what was typed after an error
    return render_template("hcp/medication_form.html", patient=patient, editing=None,
                           values=request.form, **medications.form_context())


@bp.route("/patient/<int:patient_id>/medications/<int:pm_id>/change", methods=["GET", "POST"])
@login_required
def change_medication(patient_id, pm_id):
    guard = _require_hcp()
    if guard:
        return guard
    patient = _assigned_patient(patient_id)
    pm = _patients_medication(patient, pm_id)
    if not medications.is_current(pm):
        flash("This medication has already stopped. Add a new one instead.", "warning")
        return redirect(url_for("hcp.patient_dashboard", patient_id=patient_id))

    if request.method == "POST":
        form = request.form.copy()
        form["medication_id"] = str(pm.medication_id)   # the drug itself can't change (stop + add instead)
        form.pop("started_date", None)                 # a changed dose starts today
        error, data = medications.parse_medication_form(form)
        photo = None
        if not error:
            error, photo = _uploaded_photo()
        if error:
            flash(error, "error")
            return render_template("hcp/medication_form.html", patient=patient, editing=pm,
                                   values=form, **medications.form_context())
        new, replaced = medications.change(pm, data, current_user, new_photo=photo,
                                           remove_photo=bool(request.form.get("remove_photo")))
        if replaced:
            flash(f"New prescription from today: {new.dose_label()}, {len(data['times'])}x a day. "
                  f"The old one has stopped; its dose history is kept.", "success")
        else:
            flash(f"Updated {new.dose_label()}.", "success")
        return redirect(url_for("hcp.patient_dashboard", patient_id=patient_id))

    return render_template("hcp/medication_form.html", patient=patient, editing=pm,
                           values=medications.form_values(pm), **medications.form_context())


@bp.route("/patient/<int:patient_id>/medications/<int:pm_id>/stop", methods=["POST"])
@login_required
def stop_medication(patient_id, pm_id):
    guard = _require_hcp()
    if guard:
        return guard
    patient = _assigned_patient(patient_id)
    pm = _patients_medication(patient, pm_id)
    medications.stop(pm)
    flash(f"Stopped {pm.dose_label()} for {patient.name}. It no longer shows in their doses; "
          f"its history is kept.", "success")
    return redirect(url_for("hcp.patient_dashboard", patient_id=patient_id))


@bp.route("/patient/<int:patient_id>/insights/update", methods=["POST"])
@login_required
def update_insights(patient_id):
    """Re-run the ML component for one ASSIGNED patient (anyone else -> 404)."""
    guard = _require_hcp()
    if guard:
        return guard
    patient = Patient.query.filter_by(patient_id=patient_id, hcp_id=current_user.hcp_id).first_or_404()
    flash(insights_message(analyze_patient(patient)), "success")
    return redirect(url_for("hcp.patient_dashboard", patient_id=patient_id))
