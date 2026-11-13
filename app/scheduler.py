"""
The Scheduler: a system process that runs background jobs on a timer (APScheduler).
Started only by run.py, i.e. by the web server, never by tests, `flask` CLI commands or the
seed scripts (they all call create_app() too, but don't need background jobs).

Job: expire unanswered caregiver link requests after 24 h (app/links.py), every 15 minutes.
"""
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler

from app.links import expire_old_requests

EXPIRY_CHECK_MINUTES = 15


def start_scheduler(app):
    scheduler = BackgroundScheduler(daemon=True)

    def expire_requests_job():
        with app.app_context():            # the job needs the app's database settings
            expired = expire_old_requests()
            if expired:
                app.logger.info("Scheduler: %d caregiver request(s) expired", expired)

    scheduler.add_job(expire_requests_job, "interval", minutes=EXPIRY_CHECK_MINUTES,
                      id="expire_caregiver_requests", next_run_time=datetime.now())   # once at start-up too
    scheduler.start()
    return scheduler
