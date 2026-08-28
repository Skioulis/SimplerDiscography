"""The admin «Επαναφορά βάσης» flow: upload, review, then apply.

Restoring is two requests on purpose. The upload is staged and inspected first,
so the confirmation shows real row counts rather than just a filename, and the
live database is snapshotted before anything is overwritten.
"""

import io
import os
import sqlite3

from flask_migrate import upgrade

from app import create_app
from dataio import BACKUP_PREFIX, backup_database, list_backups
from extensions import db
from models import Biography, Disc45, Disc78, Song


def _make_upload_db(tmp_path, name="upload.db", songs=2, discs=1, with_bios=True):
    """Build a valid backup file with known contents."""
    path = tmp_path / name
    src = create_app({
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{path}",
        "DB_PATH": str(path), "TESTING": True,
    })
    with src.app_context():
        upgrade()
        db.session.add_all(
            [Song(id=i, title=f"ΑΝΤΙΓΡΑΦΟ {i}") for i in range(1, songs + 1)]
            + [Disc45(id=i, title=f"45 ΑΝΤΙΓΡΑΦΟ {i}") for i in range(1, discs + 1)]
        )
        if with_bios:
            db.session.add(Biography(id=1, name="ΒΙΟΓΡΑΦΙΑ ΑΝΤΙΓΡΑΦΟΥ"))
        db.session.commit()
        db.session.remove()
    return path


def _seed_live():
    db.session.add_all([
        Song(id=1, title="ΖΩΝΤΑΝΟ ΤΡΑΓΟΥΔΙ"),
        Disc45(id=1, title="ΖΩΝΤΑΝΟ 45"),
        Disc78(id=1, title="ΖΩΝΤΑΝΟ 78"),
    ])
    db.session.commit()


# --------------------------------------------------------------------------- #
# Access control
# --------------------------------------------------------------------------- #

def test_restore_is_disabled_without_an_admin_password(app):
    assert app.test_client().get("/admin/restore").status_code == 503


def test_restore_requires_a_login(admin_app):
    r = admin_app.test_client().get("/admin/restore")
    assert r.status_code == 302
    assert "/admin/login" in r.headers["Location"]


def test_apply_requires_a_login(admin_app):
    r = admin_app.test_client().post("/admin/restore/apply")
    assert r.status_code == 302


# --------------------------------------------------------------------------- #
# The page
# --------------------------------------------------------------------------- #

def test_page_shows_the_current_row_counts(admin_app, admin_client):
    _seed_live()
    html = admin_client.get("/admin/restore").get_data(as_text=True)
    assert "Επαναφορά ολόκληρης της βάσης" in html
    assert "Τραγούδια" in html and "45άρια (ΜΑΝΙΑΤΗ)" in html


# --------------------------------------------------------------------------- #
# Step 1 — inspect
# --------------------------------------------------------------------------- #

def test_inspect_rejects_a_non_database_file(admin_client):
    r = admin_client.post("/admin/restore/inspect", data={
        "db": (io.BytesIO(b"just some text, not sqlite"), "notes.txt")})
    assert r.status_code == 400
    assert "δεν είναι αρχείο βάσης" in r.get_json()["message"]


def test_inspect_rejects_a_zero_byte_file(admin_client, tmp_path):
    """sqlite3.connect() alone writes nothing, so the file has no header yet."""
    path = tmp_path / "empty.db"
    sqlite3.connect(path).close()
    assert path.stat().st_size == 0
    with open(path, "rb") as fh:
        r = admin_client.post("/admin/restore/inspect",
                              data={"db": (io.BytesIO(fh.read()), "empty.db")})
    assert r.status_code == 400
    assert "δεν είναι αρχείο βάσης" in r.get_json()["message"]


def test_inspect_rejects_a_real_database_without_song(admin_client, tmp_path):
    """A valid SQLite file that isn't one of ours is named as such."""
    path = tmp_path / "other.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE unrelated (id INTEGER PRIMARY KEY, note TEXT)")
    con.execute("INSERT INTO unrelated (note) VALUES ('not a discography')")
    con.commit()
    con.close()
    with open(path, "rb") as fh:
        r = admin_client.post("/admin/restore/inspect",
                              data={"db": (io.BytesIO(fh.read()), "other.db")})
    assert r.status_code == 400
    assert "song" in r.get_json()["message"]


def test_inspect_rejects_an_empty_submission(admin_client):
    r = admin_client.post("/admin/restore/inspect", data={})
    assert r.status_code == 400


def test_inspect_reports_the_diff_without_changing_anything(
        admin_app, admin_client, tmp_path):
    _seed_live()
    upload = _make_upload_db(tmp_path, songs=5, discs=3)

    with open(upload, "rb") as fh:
        data = admin_client.post("/admin/restore/inspect", data={
            "db": (io.BytesIO(fh.read()), "backup.db")}).get_json()

    assert data["ok"] is True
    assert data["filename"] == "backup.db"
    by_table = {a["table"]: a for a in data["archives"]}
    assert by_table["song"]["current"] == 1 and by_table["song"]["incoming"] == 5
    assert by_table["disc45"]["current"] == 1 and by_table["disc45"]["incoming"] == 3
    assert by_table["disc78"]["incoming"] == 0
    assert data["schema_matches"] is True

    # Nothing applied yet.
    assert db.session.get(Song, 1).title == "ΖΩΝΤΑΝΟ ΤΡΑΓΟΥΔΙ"


def test_inspect_flags_archives_missing_from_the_upload(
        admin_app, admin_client, tmp_path):
    """An archive absent from the file is marked as kept, not as emptied."""
    upload = _make_upload_db(tmp_path, with_bios=True)
    # Drop biography from the upload to simulate an older backup.
    con = sqlite3.connect(upload)
    for name in ("bio_ai", "bio_ad", "bio_au"):
        con.execute(f"DROP TRIGGER IF EXISTS {name}")
    con.execute("DROP TABLE IF EXISTS bio_fts")
    con.execute("DROP TABLE IF EXISTS biography")
    con.commit()
    con.close()

    with open(upload, "rb") as fh:
        data = admin_client.post("/admin/restore/inspect", data={
            "db": (io.BytesIO(fh.read()), "old.db")}).get_json()

    bios = next(a for a in data["archives"] if a["table"] == "biography")
    assert bios["kept"] is True
    assert bios["incoming"] is None


# --------------------------------------------------------------------------- #
# Step 2 — apply
# --------------------------------------------------------------------------- #

def test_apply_without_a_staged_upload_is_refused(admin_client):
    r = admin_client.post("/admin/restore/apply")
    assert r.status_code == 400
    assert "Ανεβάστε το ξανά" in r.get_json()["message"]


def test_apply_restores_and_snapshots_first(admin_app, admin_client, tmp_path):
    _seed_live()
    upload = _make_upload_db(tmp_path, songs=4, discs=2)
    db_path = admin_app.config["DB_PATH"]

    with open(upload, "rb") as fh:
        assert admin_client.post("/admin/restore/inspect", data={
            "db": (io.BytesIO(fh.read()), "backup.db")}).get_json()["ok"]

    result = admin_client.post("/admin/restore/apply").get_json()
    assert result["ok"] is True, result

    # Rows came from the upload.
    assert db.session.get(Song, 4).title == "ΑΝΤΙΓΡΑΦΟ 4"
    assert db.session.get(Song, 1).title == "ΑΝΤΙΓΡΑΦΟ 1"
    assert db.session.get(Disc78, 1) is None          # replaced by an empty table

    # A snapshot of the pre-restore state exists and still holds the old rows.
    snapshots = list_backups(db_path)
    assert len(snapshots) == 1
    snap = os.path.join(os.path.dirname(db_path), snapshots[0]["name"])
    con = sqlite3.connect(snap)
    (old_title,) = con.execute("SELECT title FROM song WHERE id=1").fetchone()
    con.close()
    assert old_title == "ΖΩΝΤΑΝΟ ΤΡΑΓΟΥΔΙ"
    assert snapshots[0]["name"] in result["message"]


def test_apply_leaves_no_staging_file_behind(admin_app, admin_client, tmp_path):
    upload = _make_upload_db(tmp_path)
    with open(upload, "rb") as fh:
        admin_client.post("/admin/restore/inspect", data={
            "db": (io.BytesIO(fh.read()), "backup.db")})
    admin_client.post("/admin/restore/apply")

    directory = os.path.dirname(admin_app.config["DB_PATH"])
    assert [f for f in os.listdir(directory) if f.startswith("restore-staging-")] == []


def test_cancel_discards_the_staged_upload(admin_app, admin_client, tmp_path):
    upload = _make_upload_db(tmp_path)
    with open(upload, "rb") as fh:
        admin_client.post("/admin/restore/inspect", data={
            "db": (io.BytesIO(fh.read()), "backup.db")})
    directory = os.path.dirname(admin_app.config["DB_PATH"])
    assert [f for f in os.listdir(directory) if f.startswith("restore-staging-")]

    admin_client.post("/admin/restore/cancel")
    assert [f for f in os.listdir(directory) if f.startswith("restore-staging-")] == []

    # And applying afterwards is refused rather than restoring something stale.
    assert admin_client.post("/admin/restore/apply").status_code == 400


def test_reopening_the_page_abandons_a_staged_upload(admin_app, admin_client, tmp_path):
    upload = _make_upload_db(tmp_path)
    with open(upload, "rb") as fh:
        admin_client.post("/admin/restore/inspect", data={
            "db": (io.BytesIO(fh.read()), "backup.db")})
    admin_client.get("/admin/restore")
    assert admin_client.post("/admin/restore/apply").status_code == 400


# --------------------------------------------------------------------------- #
# Snapshots
# --------------------------------------------------------------------------- #

def test_backups_are_pruned_to_the_limit(admin_app):
    db_path = admin_app.config["DB_PATH"]
    for _ in range(5):
        backup_database(db_path, keep=3)
    assert len(list_backups(db_path)) == 3


def test_backup_refuses_when_the_volume_is_full(admin_app, monkeypatch):
    import shutil as shutil_mod

    import dataio

    class Full:
        free = 0
        total = used = 1

    monkeypatch.setattr(dataio.shutil, "disk_usage", lambda _p: Full)
    try:
        backup_database(admin_app.config["DB_PATH"])
    except OSError as exc:
        assert "χώρος" in str(exc)
    else:
        raise AssertionError("expected OSError when the volume is full")


# --------------------------------------------------------------------------- #
# The CSV tab no longer accepts a .db
# --------------------------------------------------------------------------- #

def test_csv_import_points_a_db_upload_at_the_restore_tab(
        admin_app, admin_client, tmp_path):
    upload = _make_upload_db(tmp_path)
    with open(upload, "rb") as fh:
        r = admin_client.post(
            "/admin/import?ds=songs",
            data={"csv": (io.BytesIO(fh.read()), "backup.db")},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
    assert r.status_code == 400
    assert "Επαναφορά βάσης" in r.get_json()["message"]
