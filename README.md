# Project Tracker (CSDB & Platbricks)

Small web app to follow up customer projects for **CSDB Multiclient & Kubernetes** and
**Platbricks Health Care Suite**: customers and contacts, contracts (effort, man-day rate,
scope, SharePoint link), CMOs, serial numbers, pre-project steps, PM tasks with
serial/parallel dependencies, invoices and credit notes, files, notes and a full change history.

Stack: Python 3.13, Flask, SQLAlchemy, SQLite (on a Fly.io volume), server-rendered HTML.
No JavaScript build, no external services.

## Features

- **Login** for each colleague; admins manage users (Settings > Users).
- **Customers** with contacts; tick "invoice contact" for the person who receives invoices.
- **Projects** created from a template: pre-project checklist (contract signed, create project
  with the controlling team, upload contract to SharePoint) and the PM task list are copied in.
- **Dependencies**: each task lists the tasks it waits on. Tasks with the same "step" number
  run in parallel. Configure defaults per template in Settings > Project templates
  (per task, or "All in series" / "All in parallel" per phase), and adjust per project.
- **Contracts**: reference, dates, effort (man-days), man-day rate or fixed price, scope,
  payment terms, SharePoint link. Contract value = fixed price or effort x rate.
- **Invoices & credit notes** per project with status (planned, to issue, issued, sent, paid,
  cancelled); credit notes link to the invoice they correct. Project totals show invoiced,
  credited, net, paid and remaining to invoice. The Invoices page filters and exports CSV.
- **Dashboard**: open projects with progress and next steps, my tasks, ready-to-start tasks,
  overdue/blocked tasks, invoices to issue in the next 14 days and unpaid invoices.
- **History**: every create/update/delete is logged with user, time and old/new values.
  Deleted records stay in the history. Projects are closed rather than deleted.
- **Files** per project (contract copy, documentation) and **notes** for meeting follow-up.
- **Backup**: admins can download the whole database from Settings.

## Run locally

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
export FLASK_APP=app:create_app
flask db upgrade
ADMIN_USERNAME=admin ADMIN_PASSWORD='choose-a-password' flask seed
flask run   # http://127.0.0.1:5000
pytest      # tests
```

## Deploy to Fly.io

One-time setup (needs a Fly.io account and `flyctl`, https://fly.io/docs/flyctl/install/):

```bash
fly auth login
fly launch --no-deploy --copy-config --name <your-app-name> --region mad
fly volumes create tracker_data --size 1 --region mad
fly secrets set SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_hex(32))')" \
                ADMIN_USERNAME=fernando ADMIN_PASSWORD='choose-a-strong-password' ADMIN_NAME='Fernando'
fly deploy
```

The admin is created on first boot only (when there are no users). Log in, then add
Antonio and Jose in Settings > Users and remove `ADMIN_PASSWORD` with `fly secrets unset ADMIN_PASSWORD`.

Later deploys: `fly deploy`. Database migrations run automatically on boot (`start.sh`).

**Notes**
- Keep exactly **one machine**: the database is a SQLite file on the volume. Fly may create
  two machines on first deploy; run `fly scale count 1` if so.
- The machine stops when idle and starts on the next request (first page takes a few seconds).
  Set `min_machines_running = 1` in `fly.toml` to keep it always on.
- Fly takes daily volume snapshots (kept 14 days, see `fly.toml`). The Settings page also
  offers a manual database download.
- To deploy automatically from GitHub, add a `FLY_API_TOKEN` secret (`fly tokens create deploy`)
  and use the workflow in `.github/workflows/fly-deploy.yml`.

## Changing the data model

Edit `app/models.py`, then:

```bash
flask db migrate -m "describe the change"
flask db upgrade
```

Commit the new file in `migrations/versions/`.
