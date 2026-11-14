import os

from app import create_app
from app.scheduler import start_scheduler

app = create_app()

if __name__ == "__main__":
    # In debug mode Flask runs this file twice (a file watcher + the real server). Start the
    # Scheduler (background jobs, e.g. expiring caregiver requests) only in the real server,
    # so each job runs once.
    if os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        start_scheduler(app)
    app.run(debug=True)

