#!/bin/sh
set -e

echo "[entrypoint] Applying database migrations..."
flask db upgrade

# Which archives still need seeding? An archive is seeded when its table is
# empty AND its CSV is present in the image. Checking each one separately
# matters on upgrades: a database that already holds songs may still be missing
# an archive added by a later release.
#
# Diagnostics go to stderr; stdout carries only the dataset keys to import.
TO_SEED=$(python - <<'PY'
import os
import sys

from app import app
from dataio import record_count
from datasets import DATASET_LIST

pending = []
with app.app_context():
    for dataset in DATASET_LIST:
        csv_path = os.path.join("files", dataset.csv_filename)
        count = record_count(dataset)
        if count:
            print(f"[entrypoint]   {dataset.title}: {count} rows, skipping.",
                  file=sys.stderr)
        elif not os.path.exists(csv_path):
            print(f"[entrypoint]   {dataset.title}: empty, but {csv_path} is not"
                  " in the image — skipping. Copy the CSV into files/ and"
                  " rebuild, or import it from /admin/import.", file=sys.stderr)
        else:
            print(f"[entrypoint]   {dataset.title}: empty, will import.",
                  file=sys.stderr)
            pending.append(dataset.key)
print(" ".join(pending))
PY
)

if [ -n "$TO_SEED" ]; then
    echo "[entrypoint] Importing archives: $TO_SEED"
    # Unquoted on purpose: the keys are a space-separated argument list.
    python import_csv.py $TO_SEED
else
    echo "[entrypoint] Nothing to import."
fi

echo "[entrypoint] Starting gunicorn..."
exec gunicorn --bind 0.0.0.0:5000 --workers "${WEB_CONCURRENCY:-3}" --timeout 60 app:app
