"""Restoring a .db upload across all four archives.

Before this change validate/restore knew only about `song`, so uploading a
backup silently replaced the songs and left the other three archives as they
were, with no indication that it had done so.
"""

import sqlite3

from flask_migrate import upgrade

from app import create_app
from dataio import replace_from_db, validate_sqlite_db
from datasets import BIOS, DISC45, DISC78, SONGS
from extensions import db
from models import Biography, Disc45, Disc78, Song


def _build_source(tmp_path, name="source.db", with_new_archives=True):
    """A second database, built through the real migrations."""
    path = tmp_path / name
    src = create_app({
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{path}",
        "DB_PATH": str(path), "TESTING": True,
    })
    with src.app_context():
        upgrade()
        db.session.add(Song(id=1, title="ΤΡΑΓΟΥΔΙ ΑΠΟ ΤΟ ΑΝΤΙΓΡΑΦΟ"))
        if with_new_archives:
            db.session.add_all([
                Disc45(id=10, title="45 ΑΠΟ ΤΟ ΑΝΤΙΓΡΑΦΟ"),
                Disc78(id=20, title="78 ΑΠΟ ΤΟ ΑΝΤΙΓΡΑΦΟ"),
                Biography(id=1, name="ΒΙΟΓΡΑΦΙΑ ΑΠΟ ΤΟ ΑΝΤΙΓΡΑΦΟ"),
            ])
        db.session.commit()
        db.session.remove()
    if not with_new_archives:
        # Simulate a backup taken before the new archives existed.
        con = sqlite3.connect(path)
        for prefix in ("disc45", "disc78", "bio"):
            for suffix in ("ai", "ad", "au"):
                con.execute(f"DROP TRIGGER IF EXISTS {prefix}_{suffix}")
        for t in ("disc45_fts", "disc78_fts", "bio_fts",
                  "disc45", "disc78", "biography"):
            con.execute(f"DROP TABLE IF EXISTS {t}")
        con.commit()
        con.close()
    return str(path)


def test_a_full_backup_validates(app, tmp_path):
    ok, msg = validate_sqlite_db(_build_source(tmp_path))
    assert ok, msg


def test_a_non_sqlite_file_is_rejected(app, tmp_path):
    junk = tmp_path / "notadb.db"
    junk.write_bytes(b"this is not a database" * 100)
    ok, msg = validate_sqlite_db(str(junk))
    assert not ok
    assert "SQLite" in msg


def test_a_database_without_song_is_rejected(app, tmp_path):
    path = tmp_path / "empty.db"
    sqlite3.connect(path).close()
    ok, msg = validate_sqlite_db(str(path))
    assert not ok
    assert "song" in msg


def test_restore_replaces_every_archive(app, db_path, tmp_path):
    db.session.add_all([
        Song(id=99, title="ΠΑΛΙΟ ΤΡΑΓΟΥΔΙ"),
        Disc45(id=99, title="ΠΑΛΙΟ 45"),
        Disc78(id=99, title="ΠΑΛΙΟ 78"),
        Biography(id=99, name="ΠΑΛΙΑ ΒΙΟΓΡΑΦΙΑ"),
    ])
    db.session.commit()
    db.session.remove()

    src_path = _build_source(tmp_path)
    counts, skipped = replace_from_db(src_path, db_path)

    assert skipped == []
    assert counts == {"song": 1, "disc45": 1, "disc78": 1, "biography": 1}

    # Every archive now holds the uploaded rows, not the old ones.
    assert db.session.get(Song, 1).title == "ΤΡΑΓΟΥΔΙ ΑΠΟ ΤΟ ΑΝΤΙΓΡΑΦΟ"
    assert db.session.get(Disc45, 10).title == "45 ΑΠΟ ΤΟ ΑΝΤΙΓΡΑΦΟ"
    assert db.session.get(Disc78, 20).title == "78 ΑΠΟ ΤΟ ΑΝΤΙΓΡΑΦΟ"
    assert db.session.get(Biography, 1).name == "ΒΙΟΓΡΑΦΙΑ ΑΠΟ ΤΟ ΑΝΤΙΓΡΑΦΟ"
    for model in (Song, Disc45, Disc78, Biography):
        assert db.session.get(model, 99) is None


def test_restore_rebuilds_the_search_indexes(app, db_path, tmp_path):
    """Rows arrive by raw INSERT, so the FTS triggers must still fire."""
    from sqlalchemy import text

    src_path = _build_source(tmp_path)
    replace_from_db(src_path, db_path)

    for dataset in (SONGS, DISC45, DISC78, BIOS):
        n = db.session.execute(
            text(f"SELECT COUNT(*) FROM {dataset.fts_table}")).scalar_one()
        assert n == 1, f"{dataset.fts_table} not in step after restore"

    found = db.session.execute(
        text("SELECT COUNT(*) FROM disc45_fts WHERE disc45_fts MATCH :q"),
        {"q": "αντιγραφο*"},
    ).scalar_one()
    assert found == 1


def test_an_older_backup_leaves_the_new_archives_alone(app, db_path, tmp_path):
    """A .db predating these archives restores songs and reports the rest.

    Silently emptying archives the upload knows nothing about would be worse
    than leaving them, so they are kept and named in the result.
    """
    db.session.add_all([
        Song(id=99, title="ΠΑΛΙΟ ΤΡΑΓΟΥΔΙ"),
        Disc45(id=99, title="ΔΙΑΤΗΡΗΤΕΟ 45"),
    ])
    db.session.commit()
    db.session.remove()

    src_path = _build_source(tmp_path, "old.db", with_new_archives=False)
    ok, msg = validate_sqlite_db(src_path)
    assert ok, msg

    counts, skipped = replace_from_db(src_path, db_path)
    assert set(skipped) == {"disc45", "disc78", "biography"}
    assert counts == {"song": 1}

    assert db.session.get(Song, 1).title == "ΤΡΑΓΟΥΔΙ ΑΠΟ ΤΟ ΑΝΤΙΓΡΑΦΟ"
    assert db.session.get(Disc45, 99).title == "ΔΙΑΤΗΡΗΤΕΟ 45"   # kept
