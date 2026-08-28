"""Search scoping: per-archive by default, all archives on request."""

from extensions import db
from models import Biography, Disc45, Disc78, Song


def _seed():
    db.session.add_all([
        Song(title="Τσιτσάνης τραγούδι", composer="Τσιτσάνης"),
        Disc45(id=1, title="ΤΣΙΤΣΑΝΗΣ 45", creators="Τσιτσάνης", company="Minos"),
        Disc78(id=1, title="ΤΣΙΤΣΑΝΗΣ 78", creators="Τσιτσάνης", company="Columbia"),
        Biography(name="Τσιτσάνης Βασίλης", capacity="Συνθέτης"),
        Disc45(id=2, title="ΑΣΧΕΤΟ", creators="Άλλος"),
    ])
    db.session.commit()


def test_search_defaults_to_the_active_archive(app):
    _seed()
    data = app.test_client().get("/api/search?q=Τσιτσάνης&ds=45").get_json()
    assert data["total"] == 1
    assert len(data["groups"]) == 1
    assert data["groups"][0]["dataset"] == "45"
    assert data["groups"][0]["results"][0]["url"] == "/45/1"


def test_cross_archive_search_groups_every_archive(app):
    _seed()
    data = app.test_client().get("/api/search?q=Τσιτσάνης&scope=all").get_json()
    assert data["total"] == 4
    assert [g["dataset"] for g in data["groups"]] == ["songs", "45", "78", "bios"]
    assert all(g["total"] == 1 for g in data["groups"])


def test_cross_archive_search_omits_archives_with_no_hits(app):
    db.session.add(Disc45(id=1, title="ΜΟΝΟ ΕΔΩ"))
    db.session.commit()
    data = app.test_client().get("/api/search?q=ΜΟΝΟ&scope=all").get_json()
    assert [g["dataset"] for g in data["groups"]] == ["45"]


def test_live_search_needs_three_characters(app):
    _seed()
    assert app.test_client().get("/api/search?q=Τσ&ds=45").get_json()["total"] == 0


def test_field_scoped_search_ignores_other_fields(app):
    _seed()
    client = app.test_client()
    # "Minos" is a company, so scoping to the title finds nothing.
    assert client.get("/api/search?q=Minos&ds=45&field=company").get_json()["total"] == 1
    assert client.get("/api/search?q=Minos&ds=45&field=title").get_json()["total"] == 0


def test_search_is_accent_insensitive(app):
    _seed()
    data = app.test_client().get("/api/search?q=τσιτσανης&ds=bios").get_json()
    assert data["total"] == 1


def test_results_page_paginates_within_one_archive(app):
    db.session.add_all([Disc45(id=i, title=f"ΤΡΑΓΟΥΔΙ {i}") for i in range(1, 31)])
    db.session.commit()
    html = app.test_client().get("/search?q=ΤΡΑΓΟΥΔΙ&ds=45").get_data(as_text=True)
    assert "30 αποτελέσματα" in html
    assert "page=2" in html


def test_results_page_clamps_an_out_of_range_page(app):
    _seed()
    r = app.test_client().get("/search?q=Τσιτσάνης&ds=45&page=999")
    assert r.status_code == 200


def test_replace_find_is_scoped_to_its_archive(app):
    _seed()
    client = app.test_client()
    assert client.get("/api/replace/find?q=ΤΣΙΤΣΑΝΗΣ&ds=45").get_json()["total"] == 1
    assert client.get("/api/replace/find?q=ΤΣΙΤΣΑΝΗΣ&ds=78").get_json()["total"] == 1
    assert client.get("/api/replace/find?q=ΤΣΙΤΣΑΝΗΣ&ds=bios").get_json()["total"] == 0


def test_replace_one_edits_only_the_named_archive(app):
    _seed()
    r = app.test_client().post("/api/replace/one", json={
        "ds": "45", "id": 1, "q": "ΤΣΙΤΣΑΝΗΣ", "repl": "ΑΛΛΑΓΜΕΝΟ", "field": "title",
    })
    assert r.get_json() == {"ok": True, "changed": True}
    assert db.session.get(Disc45, 1).title == "ΑΛΛΑΓΜΕΝΟ 45"
    assert db.session.get(Disc78, 1).title == "ΤΣΙΤΣΑΝΗΣ 78"   # untouched


def test_replace_all_respects_exclusions(app):
    db.session.add_all([Disc45(id=i, title="ΠΑΛΙΟ") for i in (1, 2, 3)])
    db.session.commit()
    r = app.test_client().post("/api/replace/all", json={
        "ds": "45", "q": "ΠΑΛΙΟ", "repl": "ΝΕΟ", "field": "title", "exclude": [2],
    })
    assert r.get_json()["count"] == 2
    assert db.session.get(Disc45, 2).title == "ΠΑΛΙΟ"


def test_invalid_regex_reports_an_error(app):
    r = app.test_client().get("/api/replace/find?q=%5B&re=1&ds=45")
    assert r.status_code == 400
    assert "error" in r.get_json()
