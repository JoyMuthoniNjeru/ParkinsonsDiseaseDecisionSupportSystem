# PD Monitor -- Parkinson's Disease Monitoring DSS

Flask scaffold matching the Chapter 4 design artifacts (use case, ERD, DB
schema, class diagram, sequence diagrams). Each blueprint under `app/` is
one of the "modules" shown in the sequence diagrams.

## Setup

1. Create a virtual environment and install dependencies:
   ```
   python -m venv venv
   source venv/bin/activate   # Windows: venv\Scripts\activate
   pip install -r requirements.txt
   ```

2. Copy `.env.example` to `.env` and fill in your PostgreSQL credentials
   (you already have Postgres running locally):
   ```
   cp .env.example .env
   ```
   Create the database itself in pgAdmin or via `createdb pd_dss`.

3. Initialize the database schema:
   ```
   flask db init
   flask db migrate -m "initial schema"
   flask db upgrade
   ```

4. Seed the admin account and catalogs:
   ```
   python seed.py
   ```

5. Run the app:
   ```
   python run.py
   ```
   Visit http://127.0.0.1:5000

## What's implemented vs. stubbed

**Implemented and working:**
- All four account types (Patient/Caregiver/HCP/Admin), matching the
  concrete-table-inheritance DB schema (no shared `users` table)
- Patient: sign up, report symptoms (predefined list + 0-10 severity +
  optional notes), manage personal medication list, log adherence,
  send/revoke caregiver links, dashboard
- Rule Engine: real threshold logic (high severity, missed doses) --
  see `app/rule_engine.py` for the hardcoded default thresholds
- Caregiver: sign up, approve/decline link requests, log observations,
  view linked patient's dashboard
- HCP: submit application (pending), view assigned patients (once
  approved and once an Admin has assigned patients to them)
- Admin: single "Manage user profiles" function -- approve/reject HCP
  applications, activate/deactivate Patient/Caregiver accounts

**Stubbed / needs building next:**
- ML Component (`app/ml_component.py`) has real trend-analysis logic
  but nothing calls it yet -- it's meant to run periodically, not on a
  request. Easiest for a demo: add a CLI command or a button somewhere
  that calls `ml_component.analyze_all_patients()` manually.
- No route yet for an Admin to assign a Patient to an HCP (currently
  only settable directly in the database) -- add this to the Admin
  blueprint when you get to that part.
- No charts/graphs on any dashboard yet -- tables only. The wireframes'
  trend-chart panels aren't wired up.
- No CSRF protection, no rate limiting, no email verification -- fine
  for an academic MVP demo, but worth naming explicitly as "future
  work" in your report rather than leaving unmentioned.

## Project structure
```
app/
  patient/, caregiver/, hcp/, admin/, auth/   -- one blueprint per module
  models.py       -- SQLAlchemy models = class diagram
  rule_engine.py  -- Rule-Based Engine (system actor)
  ml_component.py -- ML Component (system actor)
  templates/      -- one folder per role, matching the wireframes
  static/css/
config.py
run.py
seed.py
```
