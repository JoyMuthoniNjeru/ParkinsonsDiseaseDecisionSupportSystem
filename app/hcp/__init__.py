from flask import Blueprint

bp = Blueprint("hcp", __name__, template_folder="../templates/hcp")

from app.hcp import routes  # noqa
