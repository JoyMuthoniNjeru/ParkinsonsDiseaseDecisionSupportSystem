import os

from app import create_app
from app.scheduler import start_scheduler

app = create_app()

# Debug mode (on by default): auto-reload on code changes + Flask's developer error page.
# Turn it off, e.g. for the demo, so the friendly "Something went wrong" page shows instead:
#   PowerShell:  $env:PD_DEBUG="0"; python run.py
DEBUG = os.environ.get("PD_DEBUG", "1") != "0"

if __name__ == "__main__":
    # The Scheduler (background jobs, e.g. expiring caregiver requests) must run exactly once.
    # With debug ON, Flask runs this file twice (a file watcher + the real server, which has
    # WERKZEUG_RUN_MAIN="true"): start it only in the real server. With debug OFF there is only
    # one process, so start it straight away.
    if not DEBUG or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        start_scheduler(app)
    app.run(debug=DEBUG)
