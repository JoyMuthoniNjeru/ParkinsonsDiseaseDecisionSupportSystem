"""
Medication photos (80% check, 12 Nov): the HCP uploads a photo with the prescription so the patient
sees name + picture of what to take.

Safety:
  - only .jpg / .jpeg / .png / .webp, and the file must really open as an image (Pillow), so a
    renamed .exe or a text file is refused;
  - every photo is re-saved as a fresh JPEG (max 800 px), which also drops anything hidden in it
    (e.g. phone GPS data);
  - random file names, stored OUTSIDE static/ (MED_PHOTO_DIR, default instance/uploads/meds), and
    served by /photos/medication/<id> only to the patient, an approved caregiver or the assigned
    HCP (anyone else -> 404);
  - requests bigger than MAX_CONTENT_LENGTH (5 MB) are refused by Flask (friendly 413 page).
"""
import os
import re
import shutil
import secrets

from flask import Blueprint, abort, current_app, send_from_directory
from flask_login import current_user, login_required
from PIL import Image, UnidentifiedImageError
from werkzeug.datastructures import FileStorage

from app.extensions import db
from app.models import CaregiverLink, MedicationReference, Patient, PatientMedication

bp = Blueprint("photos", __name__)

ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}
MAX_SIDE = 800   # px


def photo_dir():
    folder = current_app.config.get("MED_PHOTO_DIR") or os.path.join(current_app.instance_path, "uploads", "meds")
    os.makedirs(folder, exist_ok=True)
    return folder


def save_photo(file_storage):
    """Check an uploaded photo and store it. Returns (error, None) or (None, new file name)."""
    name = (file_storage.filename or "").lower()
    if "." not in name or name.rsplit(".", 1)[1] not in ALLOWED_EXTENSIONS:
        return "The photo must be a JPG, PNG or WEBP image.", None
    try:
        with Image.open(file_storage.stream) as img:
            img.verify()                          # is it really an image?
        file_storage.stream.seek(0)
        with Image.open(file_storage.stream) as img:
            img = img.convert("RGB")
            img.thumbnail((MAX_SIDE, MAX_SIDE))
            new_name = secrets.token_hex(16) + ".jpg"
            img.save(os.path.join(photo_dir(), new_name), "JPEG", quality=85)
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        return "That file isn't a photo we can open. Please choose a JPG or PNG image.", None
    return None, new_name


# ---------------------------------------------------------------------------
# Catalogue ("standard") photos: one per medication, loaded by seed.py from demo_photos/.
# Prescribing gives the prescription its OWN copy, so replacing / removing it never touches the
# catalogue photo.
# ---------------------------------------------------------------------------

PHOTO_SOURCE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "demo_photos")


def photo_slug(name):
    """"Carbidopa/Levodopa ER capsules" -> "carbidopa_levodopa_er_capsules"."""
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def find_source_photo(med_name, folder=None):
    """demo_photos/<slug>.(jpg|jpeg|png|webp) for a medication name, also trying the name without its
    last word ("rotigotine" for "Rotigotine patch"). Returns a path or None."""
    folder = folder or PHOTO_SOURCE_DIR
    slug = photo_slug(med_name)
    for stem in (slug, slug.rsplit("_", 1)[0]):
        for ext in ("jpg", "jpeg", "png", "webp"):
            path = os.path.join(folder, f"{stem}.{ext}")
            if os.path.exists(path):
                return path
    return None


def save_photo_file(path):
    """Run a photo file on disk through the same checks as an upload. Returns (error, name)."""
    with open(path, "rb") as f:
        return save_photo(FileStorage(stream=f, filename=os.path.basename(path)))


def copy_catalog_photo(medication):
    """A new copy of the medication's standard photo for a prescription (None if it has none)."""
    if not medication or not medication.photo_filename:
        return None
    source = os.path.join(photo_dir(), medication.photo_filename)
    if not os.path.exists(source):
        return None
    new_name = secrets.token_hex(16) + ".jpg"
    shutil.copyfile(source, os.path.join(photo_dir(), new_name))
    return new_name


def delete_photo_file(filename):
    if filename:
        path = os.path.join(photo_dir(), filename)
        if os.path.exists(path):
            os.remove(path)


@bp.route("/photos/catalog/<int:medication_id>")
@login_required
def catalog_photo(medication_id):
    """A medication's standard photo (general reference, no patient data): any logged-in user."""
    med = db.session.get(MedicationReference, medication_id)
    if not med or not med.photo_filename:
        abort(404)
    return send_from_directory(photo_dir(), med.photo_filename)


def release_photo(filename):
    """Delete a photo file once no medication uses it any more (a changed prescription can share
    its photo with the one it replaced)."""
    if filename and not PatientMedication.query.filter_by(photo_filename=filename).count():
        path = os.path.join(photo_dir(), filename)
        if os.path.exists(path):
            os.remove(path)


def can_view_patient(user, patient_id):
    """The patient themselves, a caregiver with an APPROVED link, or the patient's assigned HCP."""
    role = user.get_id().split(":")[0]
    if role == "patient":
        return user.patient_id == patient_id
    if role == "caregiver":
        return CaregiverLink.query.filter_by(caregiver_id=user.caregiver_id, patient_id=patient_id,
                                             status="approved").first() is not None
    if role == "hcp":
        patient = db.session.get(Patient, patient_id)
        return patient is not None and patient.hcp_id == user.hcp_id
    return False


@bp.route("/photos/medication/<int:pm_id>")
@login_required
def medication_photo(pm_id):
    pm = db.session.get(PatientMedication, pm_id)
    if not pm or not pm.photo_filename or not can_view_patient(current_user, pm.patient_id):
        abort(404)
    response = send_from_directory(photo_dir(), pm.photo_filename)
    response.headers["Cache-Control"] = "private, max-age=3600"
    return response
