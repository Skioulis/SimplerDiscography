"""Record browsing, editing and the pager over gapped primary keys."""

import pytest

from datasets import DATASET_LIST, DISC45
from extensions import db
from models import Biography, Disc45, Disc78, Song
from views import neighbours


@pytest.fixture
def gapped(app):
    """The disc archives are numbered with gaps: 1–38947 over 37,750 rows."""
    db.session.add_all([
        Disc45(id=1, title="ΠΡΩΤΟ"),
        Disc45(id=5, title="ΔΕΥΤΕΡΟ"),
        Disc45(id=9, title="ΤΡΙΤΟ"),
    ])
    db.session.commit()
    return app


def test_neighbours_skip_gaps(gapped):
    assert neighbours(DISC45, 5) == (1, 9)


def test_neighbours_at_the_ends(gapped):
    assert neighbours(DISC45, 1) == (None, 5)
    assert neighbours(DISC45, 9) == (5, None)


def test_neighbours_from_a_missing_id(gapped):
    """Ids that don't exist still resolve to the surrounding records."""
    assert neighbours(DISC45, 6) == (5, 9)


def test_pager_links_follow_the_gaps(gapped):
    html = gapped.test_client().get("/45/5").get_data(as_text=True)
    assert 'href="/45/1"' in html
    assert 'href="/45/9"' in html


def test_goto_counts_positions_not_ids(gapped):
    """3 records numbered 1, 5, 9: the 2nd one is id 5, not id 2."""
    r = gapped.test_client().get("/45/goto?n=2")
    assert r.headers["Location"] == "/45/5"


def test_goto_reaches_the_last_position(gapped):
    r = gapped.test_client().get("/45/goto?n=3")
    assert r.headers["Location"] == "/45/9"


def test_goto_clamps_above_the_range(gapped):
    r = gapped.test_client().get("/45/goto?n=999999")
    assert r.headers["Location"] == "/45/9"


def test_goto_clamps_below_the_range(gapped):
    r = gapped.test_client().get("/45/goto?n=0")
    assert r.headers["Location"] == "/45/1"


def test_goto_survives_junk_input(gapped):
    r = gapped.test_client().get("/45/goto?n=abc")
    assert r.headers["Location"] == "/45/1"


def test_goto_on_an_empty_archive_is_404(app):
    assert app.test_client().get("/45/goto?n=1").status_code == 404


def test_pager_box_holds_the_position(gapped):
    """The jump box shows where you are in the sequence, not the row's id."""
    html = gapped.test_client().get("/45/9").get_data(as_text=True)
    assert 'name="n"' in html
    assert 'value="3"' in html


def test_missing_record_is_404(app):
    assert app.test_client().get("/45/12345").status_code == 404


@pytest.mark.parametrize("dataset", DATASET_LIST, ids=lambda d: d.key)
def test_dashboard_and_new_form_render(app, dataset):
    client = app.test_client()
    assert client.get(f"/{dataset.key}/").status_code == 200
    assert client.get(f"/{dataset.key}/new").status_code == 200


def test_legacy_songs_urls_still_work(app):
    """/songs/<id> predates the dataset prefix and must keep working."""
    db.session.add(Song(title="Νοσταλγία"))
    db.session.commit()
    assert app.test_client().get("/songs/1").status_code == 200
    assert app.test_client().get("/").status_code == 200


def test_edit_saves_and_bumps_updated(app):
    db.session.add(Disc78(id=1, title="ΠΑΛΙΟΣ", company="Ε", disc_number="Μ 1",
                          creators="Δ", year="1934", genre="Ρεμπέτικο", rhythm="Ζ"))
    db.session.commit()
    before = db.session.get(Disc78, 1).updated

    r = app.test_client().post("/78/1", data={
        "title": "ΝΕΟΣ", "company": "Ε", "disc_number": "Μ 1", "creators": "Δ",
        "year": "1934", "genre": "Ρεμπέτικο", "rhythm": "Ζ",
    })
    assert r.status_code == 302

    row = db.session.get(Disc78, 1)
    assert row.title == "ΝΕΟΣ"
    assert row.updated > before
    assert "νεος" in row.search_blob


def test_new_disc_continues_the_source_numbering(app):
    """disc45/disc78 keys aren't autoincrement, so creation assigns max+1."""
    db.session.add(Disc45(id=42, title="ΥΠΑΡΧΟΝ"))
    db.session.commit()

    r = app.test_client().post("/45/new", data={
        "title": "ΝΕΟ", "company": "", "disc_number": "", "creators": "",
        "year": "", "genre": "", "rhythm": "",
    })
    assert r.headers["Location"] == "/45/43"
    assert db.session.get(Disc45, 43).title == "ΝΕΟ"


def test_new_biography_autoincrements(app):
    r = app.test_client().post("/bios/new", data={
        "name": "Νέο πρόσωπο", "place_dates": "", "capacity": "",
        "details": "", "discography": "",
    })
    assert r.headers["Location"] == "/bios/1"
    assert db.session.get(Biography, 1).name == "Νέο πρόσωπο"


def test_delete_moves_to_a_neighbour(gapped):
    r = gapped.test_client().post("/45/5/delete")
    assert r.headers["Location"] == "/45/9"
    assert db.session.get(Disc45, 5) is None


def test_delete_of_the_last_record_returns_to_the_dashboard(app):
    db.session.add(Disc45(id=1, title="ΜΟΝΟ"))
    db.session.commit()
    r = app.test_client().post("/45/1/delete")
    assert r.headers["Location"] == "/45/"
