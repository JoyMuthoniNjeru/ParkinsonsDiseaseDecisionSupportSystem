from flask import Blueprint

bp = Blueprint("caregiver", __name__, template_folder="../templates/caregiver")

from app.caregiver import routes  # noqa
