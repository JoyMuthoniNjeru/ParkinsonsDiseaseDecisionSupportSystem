from datetime import date, timedelta
from flask import Flask
from config import Config
from app.extensions import db, migrate, login_manager, csrf


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    csrf.init_app(app)

    from flask import flash, redirect, request, url_for
    from flask_wtf.csrf import CSRFError

    @app.errorhandler(CSRFError)
    def csrf_failed(error):
        """A form arrived without a valid CSRF token (e.g. sent from another website, or the
        page was opened before logging in again). Explain it instead of a bare 400 page."""
        flash("That form had expired. Please try again.", "warning")
        return redirect(request.referrer or url_for("auth.login"))

    # Friendly error pages in the app's own layout (instead of Flask's plain white page).
    # 404 is also what someone gets for a page their account can't open (e.g. a revoked
    # caregiver link, or an HCP opening a patient who isn't theirs), so the text covers both.
    from flask import render_template
    error_pages = {
        404: ("Page not found", "signpost-split",
              "This page doesn't exist, or it isn't one your account can open."),
        405: ("That didn't work", "arrow-counterclockwise",
              "This page can't be opened like that. Please go back and use the buttons on the page."),
        413: ("That file is too big", "file-earmark-x",
              "Photos can be up to 5 MB. Please choose a smaller photo and try again."),
        500: ("Something went wrong", "exclamation-triangle",
              "Sorry, something went wrong on our side. Nothing you entered just now was saved. "
              "Please go back and try again."),
    }

    def error_page(error):
        code = getattr(error, "code", 500)
        if code == 500:
            db.session.rollback()   # leave the database usable for the next page
        title, icon, message = error_pages[code]
        return render_template("errors/error.html", code=code, title=title, icon=icon,
                               message=message), code

    for code in error_pages:
        app.register_error_handler(code, error_page)

    from app.auth import bp as auth_bp
    from app.patient import bp as patient_bp
    from app.caregiver import bp as caregiver_bp
    from app.hcp import bp as hcp_bp
    from app.admin import bp as admin_bp

    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(patient_bp, url_prefix="/patient")
    app.register_blueprint(caregiver_bp, url_prefix="/caregiver")
    app.register_blueprint(hcp_bp, url_prefix="/hcp")
    app.register_blueprint(admin_bp, url_prefix="/admin")
    from app.photos import bp as photos_bp      # medication photos (80% check), access-checked
    app.register_blueprint(photos_bp)

    @app.route("/")
    def index():
        return redirect(url_for("auth.login"))

    from app.rule_engine import PATTERN_ALERT_PREFIX
    from app.dashboard_data import can_correct_dose, can_edit_symptom
    app.jinja_env.globals["PATTERN_ALERT_PREFIX"] = PATTERN_ALERT_PREFIX
    # Used by the ui.html macros to decide whether to show "Change" / "Edit" links
    app.jinja_env.globals["can_correct_dose"] = can_correct_dose
    app.jinja_env.globals["can_edit_symptom"] = can_edit_symptom
    from app.links import expires_at
    app.jinja_env.globals["link_expires_at"] = expires_at     # "Expires 14 Nov, 10:30" on pending requests

    @app.cli.command("analyze")
    def analyze():
        """Run the ML Component: rebuild every patient's Insights from their logs."""
        from app.ml_component import analyze_all_patients
        patients, insights, found = analyze_all_patients()
        print(f"Analysed {patients} patients, created {insights} insights.")
        if len(found) < 2:
            print(f"Note: PPMI baselines found: {', '.join(found) or 'none'}. The missing ones are "
                  "skipped (expected in PPMI_DATA_DIR/output, made by the analysis/ scripts).")

    import click

    @app.cli.command("send-test-email")
    @click.argument("to")
    def send_test_email(to):
        """Check the email settings in .env: flask --app run send-test-email you@example.com"""
        from app.mail import send_email
        mode = f"via {app.config['MAIL_SERVER']}" if app.config.get("MAIL_SERVER") else "console mode (MAIL_SERVER not set)"
        print(f"Sending a test email to {to}, {mode} ...")
        try:
            send_email(to, "PD Compass test email",
                       "Hello!\n\nIf you can read this, PD Compass can send email "
                       "(used for password reset links).\n\nPD Compass")
        except Exception as error:   # show the reason (wrong app password, no internet...) plainly
            print(f"Failed: {type(error).__name__}: {error}")
            raise SystemExit(1)
        print("Sent." if app.config.get("MAIL_SERVER") else "Done (printed above, not sent).")

    @app.template_filter("age")
    def age(dob):
        """Whole years since a date of birth, e.g. 68."""
        today = date.today()
        return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))

    @app.template_filter("localtime")
    def localtime(value, fmt="%Y-%m-%d %H:%M"):
        """Convert a stored UTC datetime to local time for display."""
        if value is None:
            return ""
        offset = timedelta(hours=app.config["DISPLAY_UTC_OFFSET_HOURS"])
        return (value + offset).strftime(fmt)

    return app


@login_manager.user_loader
def load_user(combined_id):
    """
    combined_id looks like "patient:3" -- Flask-Login needs one loader,
    but we have four separate tables (concrete-table inheritance, no
    shared users table -- see DB Schema notes), so the role is encoded
    in get_id() and parsed back out here.
    """
    from app.models import Patient, Caregiver, HealthcareProfessional, Admin

    role, raw_id = combined_id.split(":")
    model = {
        "patient": Patient,
        "caregiver": Caregiver,
        "hcp": HealthcareProfessional,
        "admin": Admin,
    }.get(role)
    if model is None:
        return None
    user = db.session.get(model, int(raw_id))
    # Someone already logged in is logged out on their next request if an Admin has since
    # deactivated them (patient/caregiver) or their HCP application isn't approved.
    if user is not None and getattr(user, "account_status", "active") != "active":
        return None
    if user is not None and getattr(user, "credential_status", "approved") != "approved":
        return None
    return user
