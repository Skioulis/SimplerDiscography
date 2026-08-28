"""Shared CSV / .db import logic — used by the CLI importer and the admin upload.

Every function takes a :class:`datasets.Dataset`, so the same code loads any of
the four archives. Deliberately imports only db + models + datasets (not app) to
avoid circular imports.
"""

from __future__ import annotations

import csv
import sqlite3

from sqlalchemy import func, insert, select

from datasets import DATASET_LIST, Dataset
from extensions import db

BATCH_SIZE = 5000

#: Table -> columns an uploaded .db must have for that archive to be restored.
REQUIRED_DB_COLUMNS: dict[str, set[str]] = {
    d.model.__tablename__: set(d.searchable_fields) | {"search_blob"}
    for d in DATASET_LIST
}

#: The archive that must be present for an uploaded .db to be accepted at all.
ANCHOR_TABLE = "song"


class CSVFormatError(ValueError):
    """Raised when a CSV doesn't match the expected structure."""


def read_rows(dataset: Dataset, text_stream) -> list[dict]:
    """Parse a ';'-delimited CSV text stream into insertable row dicts.

    Validates that every expected column is present (raises CSVFormatError
    otherwise). Each returned row includes the derived ``search_blob``, and, for
    archives whose export carries an id column, that id.
    """
    csv.field_size_limit(10_000_000)
    reader = csv.DictReader(text_stream, delimiter=";")
    headers = reader.fieldnames or []
    model = dataset.model

    expected = list(model.CSV_COLUMNS)
    if model.CSV_ID_COLUMN:
        expected.append(model.CSV_ID_COLUMN)
    missing = [h for h in expected if h not in headers]
    if missing:
        raise CSVFormatError("Λείπουν στήλες: " + ", ".join(missing))

    rows: list[dict] = []
    for line_no, raw in enumerate(reader, start=2):
        row = {
            attr: (raw.get(header) or "").strip()
            for header, attr in model.CSV_COLUMNS.items()
        }
        if model.CSV_ID_COLUMN:
            value = (raw.get(model.CSV_ID_COLUMN) or "").strip()
            try:
                row["id"] = int(value)
            except ValueError:
                raise CSVFormatError(
                    f"Μη έγκυρο «{model.CSV_ID_COLUMN}» στη γραμμή {line_no}: {value!r}"
                ) from None
        row["search_blob"] = model.build_search_blob(row)
        rows.append(row)
    return rows


def replace_all(dataset: Dataset, rows: list[dict]) -> int:
    """Replace every row of one archive with ``rows``; return the count.

    Runs in a single transaction and rolls back on any error, so a failed import
    never leaves a half-empty table. The archive's FTS triggers keep its index in
    sync as rows are deleted and inserted. Other archives are untouched.
    """
    model = dataset.model
    try:
        db.session.query(model).delete()
        total = 0
        for start in range(0, len(rows), BATCH_SIZE):
            batch = rows[start:start + BATCH_SIZE]
            db.session.execute(insert(model), batch)
            total += len(batch)
        db.session.commit()
        return total
    except Exception:
        db.session.rollback()
        raise


def record_count(dataset: Dataset) -> int:
    return db.session.scalar(select(func.count()).select_from(dataset.model)) or 0


def next_id(dataset: Dataset) -> int:
    """The id to give a new record in an archive with non-autoincrement keys."""
    current = db.session.scalar(select(func.max(dataset.model.id))) or 0
    return current + 1


def validate_sqlite_db(path: str) -> tuple[bool, str]:
    """Check an uploaded file is a valid SQLite DB with a compatible song table.

    Only the anchor archive (song) is required, so that a backup taken before
    the other archives existed can still be restored. Archives missing from the
    upload are reported by :func:`replace_from_db` and left untouched.
    """
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
        if ANCHOR_TABLE not in tables:
            return False, f"Το αρχείο δεν περιέχει πίνακα «{ANCHOR_TABLE}»."
        # Any archive that IS present must have the right columns.
        for table, required in REQUIRED_DB_COLUMNS.items():
            if table not in tables:
                continue
            cols = {r[1] for r in con.execute(f"PRAGMA table_info({table})").fetchall()}
            if missing := required - cols:
                return False, (f"Λείπουν στήλες στον πίνακα «{table}»: "
                               + ", ".join(sorted(missing)))
        return True, ""
    finally:
        con.close()


def replace_from_db(src_path: str, dest_path: str) -> tuple[dict[str, int], list[str]]:
    """Restore every archive present in another SQLite file, in one transaction.

    Returns ``(counts_by_table, skipped_tables)``. Only columns common to both
    copies of a table are carried over; the live FTS triggers keep each index in
    sync. Archives absent from the upload are left untouched and reported as
    skipped.

    Uses a raw connection to the live file (not a file swap), so it's safe with
    multiple workers sharing the same database.
    """
    con = sqlite3.connect(dest_path, isolation_level=None)
    try:
        con.execute("PRAGMA busy_timeout=15000")
        con.execute("ATTACH DATABASE ? AS src", (src_path,))
        src_tables = {r[0] for r in con.execute(
            "SELECT name FROM src.sqlite_master WHERE type='table'").fetchall()}

        counts: dict[str, int] = {}
        skipped: list[str] = []
        con.execute("BEGIN")
        for table in REQUIRED_DB_COLUMNS:
            if table not in src_tables:
                skipped.append(table)
                continue
            dest_cols = [r[1] for r in con.execute(f"PRAGMA table_info({table})").fetchall()]
            src_cols = {r[1] for r in con.execute(f"PRAGMA src.table_info({table})").fetchall()}
            common = [c for c in dest_cols if c in src_cols]
            collist = ", ".join(f'"{c}"' for c in common)
            con.execute(f"DELETE FROM {table}")
            con.execute(f"INSERT INTO {table} ({collist}) SELECT {collist} FROM src.{table}")
        con.execute("COMMIT")

        for table in REQUIRED_DB_COLUMNS:
            if table not in skipped:
                (counts[table],) = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
        return counts, skipped
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
