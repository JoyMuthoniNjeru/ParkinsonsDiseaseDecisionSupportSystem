"""
Step 6 of the 80% check: the HCP uploads a medication photo with the prescription; the patient
sees it next to each dose. Only real images (JPG/PNG/WEBP) are accepted, they're re-saved as a
small JPEG with a random name outside static/, and only people allowed to see the patient can
open the photo. Photos here are made-up images written to pytest's tmp_path.
"""
import io
import os

import pytest
from PIL import Image

from conftest import login, _add
from app.extensions import db
from app.models import Caregiver, HealthcareProfessional, PatientMedication


@pytest.fixture
def photo_dir(app, tmp_path, monkeypatch):
    monkeypatch.setitem(app.config, "MED_PHOTO_DIR", str(tmp_path))
    return tmp_path


def _image(fmt="PNG", size=(1600, 1200)):
    buf = io.BytesIO()
    Image.new("RGB", size, (200, 180, 220)).save(buf, fmt)
    return buf.getvalue()


def _form(med_id, photo=None, filename="box.png", **extra):
    form = {"medication_id": med_id, "dose_amount": "0.25", "dose_unit": "mg", "form": "tablet",
            "quantity_per_dose": "1", "times_per_day": "1", "dose_times": ["08:00"], **extra}
    if photo is not None:
        form["photo"] = (io.BytesIO(photo), filename)
    return form


def _prescribe(client, patient, medication, **kw):
    return client.post(f"/hcp/patient/{patient.patient_id}/medications/add",
                       data=_form(medication.medication_id, **kw), content_type="multipart/form-data",
                       follow_redirects=True)


def test_hcp_uploads_a_photo_with_the_prescription(client, hcp, patient, medication, photo_dir):
    login(client, "hcp", hcp.email)
    _prescribe(client, patient, medication, photo=_image())
    pm = PatientMedication.query.filter(PatientMedication.photo_filename.isnot(None)).one()
    saved = photo_dir / pm.photo_filename
    assert saved.exists() and pm.photo_filename.endswith(".jpg")
    with Image.open(saved) as img:                        # re-saved as a small JPEG
        assert img.format == "JPEG" and max(img.size) <= 800


@pytest.mark.parametrize("content, filename", [
    (b"MZ\x90\x00 pretend program", "setup.exe"),          # not an allowed type
    (b"just some text, not a picture", "box.jpg"),         # right name, not an image
])
def test_non_images_are_refused_and_nothing_is_saved(client, hcp, patient, medication, photo_dir, content, filename):
    login(client, "hcp", hcp.email)
    before = PatientMedication.query.count()
    response = _prescribe(client, patient, medication, photo=content, filename=filename)
    assert b"photo" in response.data.lower()
    assert PatientMedication.query.count() == before and os.listdir(photo_dir) == []


def test_a_too_big_upload_is_refused(client, app, hcp, patient, medication, photo_dir, monkeypatch):
    monkeypatch.setitem(app.config, "MAX_CONTENT_LENGTH", 2000)
    login(client, "hcp", hcp.email)
    response = client.post(f"/hcp/patient/{patient.patient_id}/medications/add",
                           data=_form(medication.medication_id, photo=_image()),
                           content_type="multipart/form-data")
    assert response.status_code == 413 and b"too big" in response.data


def _with_photo(client, hcp, patient, medication):
    login(client, "hcp", hcp.email)
    _prescribe(client, patient, medication, photo=_image())
    return PatientMedication.query.filter(PatientMedication.photo_filename.isnot(None)).one()


def test_patient_sees_the_photo_on_their_doses_and_can_open_it(client, hcp, patient, medication, photo_dir):
    pm = _with_photo(client, hcp, patient, medication)
    patient_client = client.application.test_client()
    login(patient_client, "patient", patient.email)
    assert f"/photos/medication/{pm.patient_medication_id}" in patient_client.get("/patient/medications").get_data(as_text=True)
    response = patient_client.get(f"/photos/medication/{pm.patient_medication_id}")
    assert response.status_code == 200 and response.mimetype == "image/jpeg"


def test_approved_caregiver_can_open_the_photo(client, hcp, patient, caregiver, medication, photo_dir):
    pm = _with_photo(client, hcp, patient, medication)
    c = client.application.test_client()
    login(c, "caregiver", caregiver.email)
    assert c.get(f"/photos/medication/{pm.patient_medication_id}").status_code == 200


def test_people_who_cant_see_the_patient_get_404(client, hcp, patient, other_patient, medication, photo_dir):
    pm = _with_photo(client, hcp, patient, medication)
    url = f"/photos/medication/{pm.patient_medication_id}"
    stranger = _add(Caregiver(name="Unlinked", email="unlinked@test.local"))
    other_hcp = _add(HealthcareProfessional(name="Dr Other", email="dr.other@test.local",
                                            institution="X", credential_status="approved"))
    for role, email in (("patient", other_patient.email), ("caregiver", stranger.email), ("hcp", other_hcp.email)):
        c = client.application.test_client()
        login(c, role, email)
        assert c.get(url).status_code == 404, role
    anonymous = client.application.test_client()
    assert anonymous.get(url).status_code == 302                       # sent to log in


def test_new_photo_replaces_the_old_file(client, hcp, patient, medication, photo_dir):
    pm = _with_photo(client, hcp, patient, medication)
    old = pm.photo_filename
    client.post(f"/hcp/patient/{patient.patient_id}/medications/{pm.patient_medication_id}/change",
                data=_form(medication.medication_id, photo=_image("JPEG"), filename="new.jpg"),
                content_type="multipart/form-data")
    db.session.refresh(pm)
    assert pm.photo_filename != old
    assert not (photo_dir / old).exists() and (photo_dir / pm.photo_filename).exists()


def test_remove_photo_tick(client, hcp, patient, medication, photo_dir):
    pm = _with_photo(client, hcp, patient, medication)
    old = pm.photo_filename
    client.post(f"/hcp/patient/{patient.patient_id}/medications/{pm.patient_medication_id}/change",
                data=_form(medication.medication_id, remove_photo="1"), content_type="multipart/form-data")
    db.session.refresh(pm)
    assert pm.photo_filename is None and not (photo_dir / old).exists()


def test_new_prescription_from_a_dose_change_keeps_the_photo(client, hcp, patient, medication, photo_dir):
    pm = _with_photo(client, hcp, patient, medication)
    client.post(f"/hcp/patient/{patient.patient_id}/medications/{pm.patient_medication_id}/change",
                data=_form(medication.medication_id, dose_amount="0.5"), content_type="multipart/form-data")
    newest = PatientMedication.query.order_by(PatientMedication.patient_medication_id.desc()).first()
    assert newest.patient_medication_id != pm.patient_medication_id
    assert newest.photo_filename == pm.photo_filename and (photo_dir / pm.photo_filename).exists()


def test_without_a_photo_an_icon_is_shown(client, patient, medication):
    login(client, "patient", patient.email)
    page = client.get("/patient/medications").get_data(as_text=True)
    assert "med-photo-icon" in page and "/photos/medication/" not in page


# ---------------------------------------------------------------------------
# Standard (catalogue) photos: a prescription starts with its OWN copy, still editable
# ---------------------------------------------------------------------------

def _catalog_photo(medication, photo_dir):
    """Give the medication a standard photo, as seed.py would."""
    from app.photos import save_photo_file
    src = photo_dir / "source.png"
    Image.new("RGB", (900, 900), (120, 90, 140)).save(src)
    error, name = save_photo_file(str(src))
    src.unlink()
    medication.medication.photo_filename = name
    db.session.commit()
    return name


def test_prescribing_without_upload_copies_the_standard_photo(client, hcp, patient, medication, photo_dir):
    standard = _catalog_photo(medication, photo_dir)
    login(client, "hcp", hcp.email)
    _prescribe(client, patient, medication)
    pm = PatientMedication.query.filter(PatientMedication.photo_filename.isnot(None)).one()
    assert pm.photo_filename != standard                              # its own copy
    assert (photo_dir / pm.photo_filename).exists() and (photo_dir / standard).exists()


def test_no_photo_tick_skips_the_standard_photo(client, hcp, patient, medication, photo_dir):
    _catalog_photo(medication, photo_dir)
    login(client, "hcp", hcp.email)
    _prescribe(client, patient, medication, no_photo="1")
    assert PatientMedication.query.filter(PatientMedication.photo_filename.isnot(None)).count() == 0


def test_an_upload_replaces_the_standard_photo(client, hcp, patient, medication, photo_dir):
    standard = _catalog_photo(medication, photo_dir)
    login(client, "hcp", hcp.email)
    _prescribe(client, patient, medication, photo=_image())
    pm = PatientMedication.query.filter(PatientMedication.photo_filename.isnot(None)).one()
    assert len(os.listdir(photo_dir)) == 2                            # the standard + the upload only


def test_removing_a_prescriptions_photo_keeps_the_standard_one(client, hcp, patient, medication, photo_dir):
    standard = _catalog_photo(medication, photo_dir)
    login(client, "hcp", hcp.email)
    _prescribe(client, patient, medication)
    pm = PatientMedication.query.filter(PatientMedication.photo_filename.isnot(None)).one()
    client.post(f"/hcp/patient/{patient.patient_id}/medications/{pm.patient_medication_id}/change",
                data=_form(medication.medication_id, remove_photo="1"), content_type="multipart/form-data")
    db.session.refresh(pm)
    assert pm.photo_filename is None and (photo_dir / standard).exists()


def test_prescribe_form_offers_the_standard_photo(client, hcp, patient, medication, photo_dir):
    _catalog_photo(medication, photo_dir)
    login(client, "hcp", hcp.email)
    page = client.get(f"/hcp/patient/{patient.patient_id}/medications/add").get_data(as_text=True)
    assert f"/photos/catalog/{medication.medication_id}" in page and "No photo" in page


def test_standard_photo_needs_a_login(client, medication, photo_dir):
    _catalog_photo(medication, photo_dir)
    assert client.get(f"/photos/catalog/{medication.medication_id}").status_code == 302


def test_source_photo_names_are_matched_forgivingly(tmp_path):
    from app.photos import find_source_photo
    (tmp_path / "rotigotine.jpeg").write_bytes(b"x")
    (tmp_path / "carbidopa_levodopa_er.png").write_bytes(b"x")
    assert find_source_photo("Rotigotine patch", tmp_path).endswith("rotigotine.jpeg")
    assert find_source_photo("Carbidopa/Levodopa ER capsules", tmp_path).endswith("carbidopa_levodopa_er.png")
    assert find_source_photo("Safinamide", tmp_path) is None
