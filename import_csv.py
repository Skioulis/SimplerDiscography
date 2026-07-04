"""Load files/Τραγούδια.csv into the discography database (data only).

The schema (song table, song_fts index, sync triggers) is created by the
Flask-Migrate migrations. Run those first:

    flask --app app db upgrade
    python import_csv.py

This script is idempotent: it clears any existing rows, then bulk-inserts all
records in CSV order (id = 1..N). The FTS index is populated automatically by
the AFTER INSERT trigger created in the migration.
"""

from __future__ import annotations

import csv
import os
import sys

from sqlalchemy import func, insert, inspect, select, text

from app import create_app
from extensions import db
from models import Song

CSV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "files", "Τραγούδια.csv")
BATCH_SIZE = 5000


def read_rows(csv_path: str):
    """Yield dicts (attr -> value, plus search_blob) from the CSV, in order."""
    # A couple of notes/lyrics fields are very long; lift the field-size cap.
    csv.field_size_limit(10_000_000)
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f, delimiter=";")
        header_to_attr = Song.CSV_COLUMNS
        for raw in reader:
            row = {
                attr: (raw.get(header) or "").strip()
                for header, attr in header_to_attr.items()
            }
            # Core bulk insert bypasses ORM events, so compute the blob here.
            row["search_blob"] = Song.build_search_blob(row)
            yield row


def main() -> int:
    if not os.path.exists(CSV_PATH):
        print(f"ERROR: CSV not found at {CSV_PATH}", file=sys.stderr)
        return 1

    app = create_app()
    with app.app_context():
        if not inspect(db.engine).has_table("song"):
            print(
                "ERROR: 'song' table not found. Create the schema first:\n"
                "    flask --app app db upgrade",
                file=sys.stderr,
            )
            return 1

        # Idempotent: clear existing rows (delete triggers clean the FTS index).
        # Emptying the table also resets the autoincrement, so ids restart at 1.
        db.session.query(Song).delete()
        db.session.commit()

        # Bulk-insert rows in CSV order. The AFTER INSERT trigger fills song_fts.
        total = 0
        batch: list[dict] = []
        for row in read_rows(CSV_PATH):
            batch.append(row)
            if len(batch) >= BATCH_SIZE:
                db.session.execute(insert(Song), batch)
                total += len(batch)
                batch.clear()
        if batch:
            db.session.execute(insert(Song), batch)
            total += len(batch)
        db.session.commit()

        # Verify.
        song_count = db.session.scalar(select(func.count()).select_from(Song))
        fts_count = db.session.execute(text("SELECT COUNT(*) FROM song_fts")).scalar_one()

    print(f"Inserted rows:   {total}")
    print(f"song count:      {song_count}")
    print(f"song_fts count:  {fts_count}")
    if song_count != fts_count:
        print("WARNING: FTS row count does not match song count!", file=sys.stderr)
        return 2
    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
