"""Load the archive CSVs from files/ into the database (data only).

The schema (tables, FTS indexes, sync triggers) is created by the Flask-Migrate
migrations. Run those first:

    flask --app app db upgrade
    python import_csv.py 45 78 bios      # the three ΜΑΝΙΑΤΗ / Βιογραφίες archives
    python import_csv.py all             # every archive, songs included

Dataset keys are the registry slugs: songs, 45, 78, bios.

Archives must be named explicitly: replacing an archive discards any edits made
through the UI since its CSV was exported, so which ones to overwrite is never
inferred. Each named archive has all its rows replaced with its CSV; the others
are left untouched.
"""

from __future__ import annotations

import os
import sys

from sqlalchemy import inspect, text

from app import create_app
from dataio import read_rows, record_count, replace_all
from datasets import DATASET_LIST, DATASETS
from extensions import db

FILES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "files")


def _import_one(dataset) -> int:
    """Load one archive. Returns 0 on success, non-zero on failure."""
    path = os.path.join(FILES_DIR, dataset.csv_filename)
    table = dataset.model.__tablename__

    print(f"\n{dataset.title}")
    if not os.path.exists(path):
        print(f"  ERROR: CSV not found at {path}", file=sys.stderr)
        return 1
    if not inspect(db.engine).has_table(table):
        print(
            f"  ERROR: table '{table}' not found. Create the schema first:\n"
            "    flask --app app db upgrade",
            file=sys.stderr,
        )
        return 1

    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = read_rows(dataset, f)
    total = replace_all(dataset, rows)

    rows_n = record_count(dataset)
    fts_n = db.session.execute(
        text(f"SELECT COUNT(*) FROM {dataset.fts_table}")).scalar_one()
    print(f"  inserted:            {total}")
    print(f"  {table} count:{' ' * max(1, 14 - len(table))}{rows_n}")
    print(f"  {dataset.fts_table} count:{' ' * max(1, 10 - len(dataset.fts_table))}{fts_n}")
    if rows_n != fts_n:
        print(f"  WARNING: FTS row count does not match {table} count!", file=sys.stderr)
        return 2
    print("  OK")
    return 0


USAGE = (
    "usage: python import_csv.py <dataset> [<dataset> ...]\n"
    "       python import_csv.py all\n\n"
    "Replaces every row of each named archive with the contents of its CSV.\n"
    "Available datasets: {available}\n"
)


def main(argv: list[str]) -> int:
    if not argv:
        print(USAGE.format(available=", ".join(DATASETS)), file=sys.stderr)
        return 1

    keys = [d.key for d in DATASET_LIST] if argv == ["all"] else argv
    unknown = [k for k in keys if k not in DATASETS]
    if unknown:
        print(f"ERROR: unknown dataset(s): {', '.join(unknown)}", file=sys.stderr)
        print(f"       available: {', '.join(DATASETS)}", file=sys.stderr)
        return 1

    app = create_app()
    status = 0
    with app.app_context():
        for key in keys:
            status = _import_one(DATASETS[key]) or status
    return status


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
