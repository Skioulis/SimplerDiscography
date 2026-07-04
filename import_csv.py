"""Load files/Τραγούδια.csv into the discography database (data only).

The schema (song table, song_fts index, sync triggers) is created by the
Flask-Migrate migrations. Run those first:

    flask --app app db upgrade
    python import_csv.py

Idempotent: replaces all existing rows with the CSV contents.
"""

from __future__ import annotations

import os
import sys

from sqlalchemy import inspect, text

from app import create_app
from dataio import read_song_rows, replace_all_songs, song_count
from extensions import db

CSV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "files", "Τραγούδια.csv")


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

        with open(CSV_PATH, encoding="utf-8-sig", newline="") as f:
            rows = read_song_rows(f)
        total = replace_all_songs(rows)

        song_n = song_count()
        fts_n = db.session.execute(text("SELECT COUNT(*) FROM song_fts")).scalar_one()

    print(f"Inserted rows:   {total}")
    print(f"song count:      {song_n}")
    print(f"song_fts count:  {fts_n}")
    if song_n != fts_n:
        print("WARNING: FTS row count does not match song count!", file=sys.stderr)
        return 2
    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
