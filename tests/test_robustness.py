"""Surviving rows whose TEXT columns aren't valid UTF-8.

SQLite does not validate TEXT, so a bad write can leave bytes the default
decoder refuses. Because find & replace and field-scoped search both scan the
whole archive, one such row otherwise takes those features down for every
record, not just for itself. This is not hypothetical: the production database
had an HTTP error response spliced into song 18687's ΣΗΜΕΙΩΣΕΙΣ field.
"""

import sqlite3

import pytest

from datasets import SONGS
from extensions import db
from models import Song
from views import field_hits, scan_rows

# Valid Greek text, then a truncated multi-byte char, then more valid text —
# the shape left behind when a response body is written over a UTF-8 string.
CORRUPT_NOTES = (
    "Ζεϊμπέκικος. -78άρι Columbia DG 2114. Συμπερι".encode()
    + b"\xce"
    + b"HTTP/1.1 500 Internal Server Error\r\n\r\n"
    + "δης (Το ρεμπέτικο Νο 8). Αθήνα, 1934.".encode()
)


def _insert_corrupt_row(db_path, rec_id=18687):
    """Write raw bytes past the ORM, the way the damaged row came to exist.

    The CAST matters: binding a bytes object stores a BLOB, which reads back
    without complaint and would make these tests pass for the wrong reason.
    Casting stores the same bytes as TEXT, which is what SQLite does not
    validate and what the strict decoder then rejects.
    """
    con = sqlite3.connect(db_path)
    con.execute(
        "INSERT INTO song (id, title, composer, lyricist, lyrics, archive,"
        " bibliography, notes, search_blob)"
        " VALUES (?,?,?,?,?,?,?,CAST(? AS TEXT),?)",
        (rec_id, "ΤΙΤΛΟΣ", "", "", "", "", "", CORRUPT_NOTES, ""),
    )
    con.commit()
    con.close()


def test_the_fixture_row_is_stored_as_text_not_blob(app, db_path):
    """Guard the guard: a BLOB would not exercise the decoder at all."""
    _insert_corrupt_row(db_path)
    con = sqlite3.connect(db_path)
    (kind,) = con.execute("SELECT typeof(notes) FROM song WHERE id=18687").fetchone()
    con.close()
    assert kind == "text"


def test_strict_decoding_would_fail_on_this_row(app, db_path):
    """Without the lenient text factory, scanning raises OperationalError."""
    _insert_corrupt_row(db_path)
    con = sqlite3.connect(db_path)          # default strict text factory
    try:
        with pytest.raises(sqlite3.OperationalError):
            con.execute("SELECT notes FROM song WHERE id=18687").fetchone()
    finally:
        con.close()


def test_raw_bytes_are_genuinely_undecodable():
    """Guard the fixture itself: this must be invalid UTF-8 to test anything."""
    try:
        CORRUPT_NOTES.decode("utf-8")
    except UnicodeDecodeError:
        return
    raise AssertionError("fixture is valid UTF-8, so it tests nothing")


def test_full_scan_survives_a_corrupt_row(app, db_path):
    _insert_corrupt_row(db_path)
    db.session.add(Song(id=1, title="Νοσταλγία"))
    db.session.commit()

    ids = [row["id"] for row in scan_rows(SONGS)]
    assert ids == [1, 18687]      # the scan completes instead of raising


def test_field_search_reaches_rows_after_the_corrupt_one(app, db_path):
    """The damaged row must not hide the records ordered after it."""
    _insert_corrupt_row(db_path)
    db.session.add(Song(id=99999, title="Νοσταλγία"))
    db.session.commit()

    hits = field_hits(SONGS, "νοσταλγια", "title")
    assert [h["id"] for h in hits] == [99999]


def test_corrupt_row_is_readable_and_repairable(app, db_path):
    """It renders with U+FFFD where the bad bytes were, so it can be fixed."""
    _insert_corrupt_row(db_path)
    client = app.test_client()

    page = client.get("/songs/18687")
    assert page.status_code == 200
    assert "Ζεϊμπέκικος" in page.get_data(as_text=True)

    # Saving repaired text leaves a clean, valid row behind.
    assert client.post("/songs/18687", data={
        "title": "ΤΙΤΛΟΣ", "composer": "", "lyricist": "", "lyrics": "",
        "archive": "", "bibliography": "", "notes": "Ζεϊμπέκικος. Αθήνα, 1934.",
    }).status_code == 302

    con = sqlite3.connect(db_path)
    (raw,) = con.execute("SELECT notes FROM song WHERE id=18687").fetchone()
    con.close()
    assert raw == "Ζεϊμπέκικος. Αθήνα, 1934."
