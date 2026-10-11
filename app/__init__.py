from flask import Flask
from config import Config
from app.extensions import db, migrate, login_manager


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)

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

    from flask import redirect, url_for

    @app.route("/")
    def index():
        return redirect(url_for("auth.login"))

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
    return model.query.get(int(raw_id))
