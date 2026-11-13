from flask import render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from app.caregiver import bp
from app.extensions import db
from app.validators import password_error
from app.models import Caregiver, CaregiverLink, CaregiverObservation, SEVERITY_LABELS
from app.dashboard_data import todays_doses, patient_stats, patient_overview
from app import entries
from app.links import expire_old_requests


def _require_caregiver():
    if not isinstance(current_user, Caregiver):
        flash("Please log in as a caregiver.", "error")
        return redirect(url_for("auth.login"))
    return None


@bp.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        existing = Caregiver.query.filter_by(email=request.form["email"]).first()
        if existing:
            flash("An account with that email already exists.", "error")
            return redirect(url_for("caregiver.register"))

        error = password_error(request.form["password"], request.form["confirm_password"])
        if error:
            flash(error, "error")
            return redirect(url_for("caregiver.register"))

        caregiver = Caregiver(name=request.form["name"], email=request.form["email"])
        caregiver.set_password(request.form["password"])
        db.session.add(caregiver)
        db.session.commit()
        flash("Account created. You can now log in.", "success")
        return redirect(url_for("auth.login"))

    return render_template("caregiver/register.html")


@bp.route("/requests", methods=["GET", "POST"])
@login_required
def requests_():
    guard = _require_caregiver()
    if guard:
        return guard

    expire_old_requests()          # a request is never answerable after 24 h, even between Scheduler runs
    if request.method == "POST":
        link = db.get_or_404(CaregiverLink, int(request.form["link_id"]))
        if link.caregiver_id != current_user.caregiver_id:
            flash("Not allowed.", "error")
        elif link.status != "pending":
            flash(f"This request from {link.patient.name} has expired or was already answered. "
                  f"They can send a new one.", "warning")
        else:
            link.status = "approved" if request.form["decision"] == "approve" else "declined"
            db.session.commit()
            if link.status == "approved":
                flash(f"You're now linked with {link.patient.name}.", "success")
            else:
                flash(f"Request from {link.patient.name} declined.", "success")
        return redirect(url_for("caregiver.requests_"))

    pending = CaregiverLink.query.filter_by(
        caregiver_id=current_user.caregiver_id, status="pending"
    ).all()
    approved = CaregiverLink.query.filter_by(
        caregiver_id=current_user.caregiver_id, status="approved"
    ).all()
    return render_template("caregiver/requests.html", pending=pending, approved=approved)


@bp.route("/patient/<int:patient_id>/observe", methods=["GET", "POST"])
@login_required
def observe(patient_id):
    guard = _require_caregiver()
    if guard:
        return guard

    link = CaregiverLink.query.filter_by(
        caregiver_id=current_user.caregiver_id, patient_id=patient_id, status="approved"
    ).first_or_404()

    if request.method == "POST":
        obs = CaregiverObservation(
            caregiver_id=current_user.caregiver_id,
            patient_id=patient_id,
            note=request.form["note"],
        )
        db.session.add(obs)
        db.session.commit()
        flash("Observation saved.", "success")
        return redirect(url_for("caregiver.patient_dashboard", patient_id=patient_id))

    return render_template("caregiver/observe.html", patient=link.patient)


@bp.route("/dashboard")
@login_required
def dashboard():
    guard = _require_caregiver()
    if guard:
        return guard

    expire_old_requests()
    links = CaregiverLink.query.filter_by(
        caregiver_id=current_user.caregiver_id, status="approved"
    ).all()
    pending_count = CaregiverLink.query.filter_by(
        caregiver_id=current_user.caregiver_id, status="pending"
    ).count()
    summaries = {l.patient_id: patient_stats(l.patient_id, todays_doses(l.patient_id)) for l in links}
    return render_template("caregiver/dashboard.html", links=links, pending_count=pending_count,
                           summaries=summaries)


@bp.route("/patient/<int:patient_id>/dashboard")
@login_required
def patient_dashboard(patient_id):
    guard = _require_caregiver()
    if guard:
        return guard

    link = CaregiverLink.query.filter_by(
        caregiver_id=current_user.caregiver_id, patient_id=patient_id, status="approved"
    ).first_or_404()

    return render_template("caregiver/patient_dashboard.html", patient=link.patient,
                           **patient_overview(patient_id), undo=entries.undo_once())


# ---------------------------------------------------------------------------
# Logging FOR a linked patient (80% check: "patients may be unable to log themselves").
# Approved link only (pending / declined / revoked -> 404). Same checks, Rule Engine and insight
# refresh as the patient's own entries (app/entries.py); saved with the caregiver's id, shown as
# "Logged by <caregiver>". Corrections (Change / edit / delete) stay with the patient; the
# caregiver can only Undo a dose they logged themselves, within 10 minutes.
# ---------------------------------------------------------------------------

def _approved_patient(patient_id):
    """The linked patient if this caregiver's link is approved, else 404."""
    return CaregiverLink.query.filter_by(
        caregiver_id=current_user.caregiver_id, patient_id=patient_id, status="approved"
    ).first_or_404().patient


@bp.route("/patient/<int:patient_id>/symptoms", methods=["GET", "POST"])
@login_required
def log_symptoms(patient_id):
    guard = _require_caregiver()
    if guard:
        return guard
    patient = _approved_patient(patient_id)

    if request.method == "POST":
        error, records = entries.parse_symptom_form(request.form, patient.patient_id,
                                                    caregiver_id=current_user.caregiver_id)
        if error:
            flash(error, "warning")
            return redirect(url_for("caregiver.log_symptoms", patient_id=patient_id))
        alerts = entries.save_symptoms(records, patient)
        first_name = patient.name.split()[0]
        flash(f"{entries.saved_message(len(records), alerts, whose=first_name + '’s')} "
              f"They show as logged by you.", "success")
        return redirect(url_for("caregiver.patient_dashboard", patient_id=patient_id))

    return render_template("patient/report.html", groups=entries.symptom_groups(),
                           severity_labels=SEVERITY_LABELS, for_patient=patient)


@bp.route("/patient/<int:patient_id>/doses/log", methods=["POST"])
@login_required
def log_dose(patient_id):
    guard = _require_caregiver()
    if guard:
        return guard
    patient = _approved_patient(patient_id)
    log, error = entries.log_scheduled_dose(patient, request.form.get("schedule_id", type=int),
                                            request.form.get("status"),
                                            caregiver_id=current_user.caregiver_id)
    if error:
        flash(error, "error")
    else:
        entries.remember_for_undo(log, f"{patient.name.split()[0]}’s {log.patient_medication.dose_label()} at "
                                       f"{log.schedule.dose_time:%H:%M} marked as {log.status}.")
    return redirect(url_for("caregiver.patient_dashboard", patient_id=patient_id))


@bp.route("/doses/undo", methods=["POST"])
@login_required
def undo_dose():
    """Undo a dose THIS caregiver just logged (10 min), for a patient they're still linked to."""
    guard = _require_caregiver()
    if guard:
        return guard
    log = entries.take_undo_log()
    patient_id = log.patient_medication.patient_id if log else None
    still_linked = patient_id is not None and CaregiverLink.query.filter_by(
        caregiver_id=current_user.caregiver_id, patient_id=patient_id, status="approved").first()
    if (not entries.can_still_undo(log) or log.logged_by_caregiver_id != current_user.caregiver_id
            or not still_linked):
        flash("That dose can no longer be undone.", "error")
        return redirect(url_for("caregiver.patient_dashboard", patient_id=patient_id)
                        if still_linked else url_for("caregiver.dashboard"))
    entries.delete_dose_log(log, still_linked.patient)
    flash("Undone. The dose can be logged again.", "success")
    return redirect(url_for("caregiver.patient_dashboard", patient_id=patient_id))
