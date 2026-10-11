from flask import render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from app.caregiver import bp
from app.extensions import db
from app.models import Caregiver, CaregiverLink, CaregiverObservation, SymptomRecord, Alert, Insight


def _require_caregiver():
    if not isinstance(current_user, Caregiver):
        flash("Please log in as a caregiver.")
        return redirect(url_for("auth.login"))
    return None


@bp.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        existing = Caregiver.query.filter_by(email=request.form["email"]).first()
        if existing:
            flash("An account with that email already exists.")
            return redirect(url_for("caregiver.register"))

        caregiver = Caregiver(name=request.form["name"], email=request.form["email"])
        caregiver.set_password(request.form["password"])
        db.session.add(caregiver)
        db.session.commit()
        flash("Account created. You can now log in.")
        return redirect(url_for("auth.login"))

    return render_template("caregiver/register.html")


@bp.route("/requests", methods=["GET", "POST"])
@login_required
def requests_():
    guard = _require_caregiver()
    if guard:
        return guard

    if request.method == "POST":
        link = CaregiverLink.query.get_or_404(int(request.form["link_id"]))
        if link.caregiver_id != current_user.caregiver_id:
            flash("Not allowed.")
        else:
            link.status = "approved" if request.form["decision"] == "approve" else "declined"
            db.session.commit()
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
        flash("Observation logged.")
        return redirect(url_for("caregiver.observe", patient_id=patient_id))

    return render_template("caregiver/observe.html", patient=link.patient)


@bp.route("/dashboard")
@login_required
def dashboard():
    guard = _require_caregiver()
    if guard:
        return guard

    links = CaregiverLink.query.filter_by(
        caregiver_id=current_user.caregiver_id, status="approved"
    ).all()
    return render_template("caregiver/dashboard.html", links=links)


@bp.route("/patient/<int:patient_id>/dashboard")
@login_required
def patient_dashboard(patient_id):
    guard = _require_caregiver()
    if guard:
        return guard

    link = CaregiverLink.query.filter_by(
        caregiver_id=current_user.caregiver_id, patient_id=patient_id, status="approved"
    ).first_or_404()

    symptom_records = (
        SymptomRecord.query.filter_by(patient_id=patient_id)
        .order_by(SymptomRecord.timestamp.desc()).limit(10).all()
    )
    alerts = Alert.query.filter_by(patient_id=patient_id).order_by(Alert.timestamp.desc()).limit(10).all()
    insights = Insight.query.filter_by(patient_id=patient_id).order_by(Insight.generated_at.desc()).limit(5).all()
    return render_template("caregiver/patient_dashboard.html", patient=link.patient,
                            symptom_records=symptom_records, alerts=alerts, insights=insights)
