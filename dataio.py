"""Shared CSV import logic — used by the CLI importer and the admin upload.

Deliberately imports only db + models (not app) to avoid circular imports.
"""

from __future__ import annotations

import csv

from sqlalchemy import func, insert, select

from extensions import db
from models import Song

BATCH_SIZE = 5000


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
