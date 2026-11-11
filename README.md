# MedCare Roster Loop

A Flask prototype: a roster change from a small practice is read from a messy file, checked by fixed rules,
routed by change type, approved by the plan where needed, and only marked Applied once every destination
system confirms it. All data is synthetic. See `/about` in the app for what is real and what is simulated.

## Run locally

    python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
    .venv/bin/python app.py          # http://localhost:5050
    .venv/bin/python test_flow.py    # walks the main demo flow

Deploys to Render with `render.yaml` (one gunicorn worker, because demo state is in memory).
