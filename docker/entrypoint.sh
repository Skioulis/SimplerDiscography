#!/bin/sh
set -e

echo "[entrypoint] Applying database migrations..."
flask db upgrade

ROWS=$(python - <<'PY'
from app import app
from extensions import db
from sqlalchemy import text
with app.app_context():
    print(db.session.execute(text("SELECT COUNT(*) FROM song")).scalar_one())
PY
)

if [ "$ROWS" = "0" ]; then
    echo "[entrypoint] Empty database — importing songs from CSV..."
    python import_csv.py
else
    echo "[entrypoint] Database already contains $ROWS songs; skipping import."
fi

echo "[entrypoint] Starting gunicorn..."
exec gunicorn --bind 0.0.0.0:5000 --workers "${WEB_CONCURRENCY:-3}" --timeout 60 app:app
