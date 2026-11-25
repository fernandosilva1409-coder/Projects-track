#!/bin/sh
# Runs on every boot: apply database migrations, create default data, start the web server.
set -e
mkdir -p "${DATA_DIR:-/data}/uploads"
flask db upgrade
flask seed
# One worker keeps SQLite simple and safe; threads handle concurrent requests.
exec gunicorn --bind 0.0.0.0:8080 --workers 1 --threads 8 --timeout 120 --access-logfile - "app:create_app()"
