"""Shared CSV import logic — used by the CLI importer and the admin upload.

Deliberately imports only db + models (not app) to avoid circular imports.
"""

from __future__ import annotations

import csv
import sqlite3

from sqlalchemy import func, insert, select

from extensions import db
from models import Song

BATCH_SIZE = 5000

# Columns an uploaded .db must have in its `song` table to be accepted.
REQUIRED_DB_COLUMNS = set(Song.SEARCHABLE_FIELDS) | {"search_blob"}


class CSVFormatError(ValueError):
    """Raised when a CSV doesn't match the expected structure."""


def read_song_rows(text_stream) -> list[dict]:
    """Parse a ';'-delimited CSV text stream into insertable row dicts.

    Validates that every expected column is present (raises CSVFormatError
    otherwise). Each returned row includes the derived ``search_blob``.
    """
    csv.field_size_limit(10_000_000)
    reader = csv.DictReader(text_stream, delimiter=";")
    headers = reader.fieldnames or []
    missing = [h for h in Song.CSV_COLUMNS if h not in headers]
    if missing:
        raise CSVFormatError("Λείπουν στήλες: " + ", ".join(missing))

    rows: list[dict] = []
    for raw in reader:
        row = {
            attr: (raw.get(header) or "").strip()
            for header, attr in Song.CSV_COLUMNS.items()
        }
        row["search_blob"] = Song.build_search_blob(row)
        rows.append(row)
    return rows


def replace_all_songs(rows: list[dict]) -> int:
    """Replace every song with ``rows`` in a single transaction; return the count.

    Rolls back on any error, so a failed import never leaves a half-empty table.
    The FTS triggers keep song_fts in sync as rows are deleted and inserted.
    """
    try:
        db.session.query(Song).delete()
        total = 0
        for start in range(0, len(rows), BATCH_SIZE):
            batch = rows[start:start + BATCH_SIZE]
            db.session.execute(insert(Song), batch)
            total += len(batch)
        db.session.commit()
        return total
    except Exception:
        db.session.rollback()
        raise


def song_count() -> int:
    return db.session.scalar(select(func.count()).select_from(Song)) or 0


def validate_sqlite_db(path: str) -> tuple[bool, str]:
    """Check an uploaded file is a valid SQLite DB with a compatible song table."""
    con = sqlite3.connect(path)
    try:
        try:
            check = con.execute("PRAGMA quick_check").fetchone()
        except sqlite3.DatabaseError:
            return False, "Δεν είναι έγκυρο αρχείο SQLite."
        if not check or check[0] != "ok":
            return False, "Το αρχείο SQLite φαίνεται κατεστραμμένο."
        tables = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if "song" not in tables:
            return False, "Το αρχείο δεν περιέχει πίνακα «song»."
        cols = {r[1] for r in con.execute("PRAGMA table_info(song)").fetchall()}
        missing = REQUIRED_DB_COLUMNS - cols
        if missing:
            return False, "Λείπουν στήλες στον πίνακα «song»: " + ", ".join(sorted(missing))
        return True, ""
    finally:
        con.close()


def replace_songs_from_db(src_path: str, dest_path: str) -> int:
    """Copy all song rows from another SQLite file into the live DB, in one
    transaction. Only columns common to both `song` tables are copied; the FTS
    triggers on the live DB keep song_fts in sync. Returns the row count.

    Uses a raw connection to the live file (not a file swap), so it's safe with
    multiple workers sharing the same database.
    """
    con = sqlite3.connect(dest_path, isolation_level=None)
    try:
        con.execute("PRAGMA busy_timeout=15000")
        con.execute("ATTACH DATABASE ? AS src", (src_path,))
        dest_cols = [r[1] for r in con.execute("PRAGMA table_info(song)").fetchall()]
        src_cols = {r[1] for r in con.execute("PRAGMA src.table_info(song)").fetchall()}
        common = [c for c in dest_cols if c in src_cols]
        collist = ", ".join(f'"{c}"' for c in common)
        con.execute("BEGIN")
        con.execute("DELETE FROM song")
        con.execute(f"INSERT INTO song ({collist}) SELECT {collist} FROM src.song")
        con.execute("COMMIT")
        (count,) = con.execute("SELECT COUNT(*) FROM song").fetchone()
        return count
    except Exception:
        try:
            con.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    finally:
        try:
            con.execute("DETACH DATABASE src")
        except sqlite3.Error:
            pass
        con.close()
