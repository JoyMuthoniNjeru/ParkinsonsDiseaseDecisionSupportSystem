"""
Caregiver link requests expire (80% check, 12 Nov): a request the caregiver hasn't answered
within 24 hours becomes "expired". Approved links never expire (the patient revokes them).

`CaregiverLink.linked_date` is the time the request was sent (re-sending resets it).
Expiry is applied in two places, so a request is never approvable after 24 h:
  - the Scheduler (app/scheduler.py) runs expire_old_requests() every 15 minutes, and
  - the request pages call it before showing or answering requests (no gap between job runs).
"""
from datetime import datetime, timedelta

from app.extensions import db
from app.models import CaregiverLink

REQUEST_EXPIRY = timedelta(hours=24)


def expire_old_requests(now=None):
    """Mark every pending request older than 24 h as expired. Returns how many were expired.
    `now` can be passed in (tests use a fixed time)."""
    now = now or datetime.utcnow()
    count = (CaregiverLink.query
             .filter(CaregiverLink.status == "pending", CaregiverLink.linked_date <= now - REQUEST_EXPIRY)
             .update({"status": "expired"}, synchronize_session=False))
    if count:
        db.session.commit()
    return count


def expires_at(link):
    """When a pending request will expire (UTC)."""
    return link.linked_date + REQUEST_EXPIRY
