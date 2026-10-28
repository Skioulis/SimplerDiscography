"""Search scoping: per-archive by default, all archives on request."""

import pytest

from datasets import DATASET_LIST
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


# --------------------------------------------------------------------------- #
# Replace preview
#
# The preview mirrors dataset.layout. When a layout gains a new cell type the
# preview has to learn it too: a Stack was once rendered as an empty, unlabelled
# box, so half the record silently vanished from the replace tool.
# --------------------------------------------------------------------------- #

def _text_of(html: str) -> str:
    """Visible text of the preview — matches are wrapped in <mark>, so a value
    is split across tags and can only be compared once the markup is gone."""
    import re
    return re.sub(r"<[^>]+>", "", html)


@pytest.mark.parametrize("dataset", DATASET_LIST, ids=lambda d: d.key)
def test_preview_renders_every_field_of_every_archive(app, dataset):
    model = dataset.model
    values = {attr: f"τιμή {attr}" for attr in dataset.searchable_fields}
    record = model(**values)
    if model.CSV_ID_COLUMN:
        record.id = 1
    db.session.add(record)
    db.session.commit()

    r = app.test_client().get(
        f"/api/replace/preview?q=τιμή&ds={dataset.key}&id={record.id}")
    assert r.status_code == 200
    html = r.get_json()["html"]
    text = _text_of(html)

    # Every searchable field appears, with its label and its value.
    for attr in dataset.searchable_fields:
        assert f">{dataset.label(attr)}</label>" in html, f"{dataset.key}: {attr} label missing"
        assert values[attr] in text, f"{dataset.key}: {attr} value missing"

    # And nothing renders as a value box without a label beside it.
    boxes = html.count('class="replace-view__value')
    labels = html.count('class="field-label"')
    assert boxes == labels == len(dataset.searchable_fields)


def test_preview_covers_fields_nested_in_a_stack(app):
    """Fields inside a Stack are the ones a flat loop over rows would drop."""
    from datasets import SONGS

    stacked = [f.attr
               for row in SONGS.layout
               for cell in row if getattr(cell, "is_stack", False)
               for f in cell.fields]
    assert stacked, "the songs layout no longer uses a Stack; update this test"

    db.session.add(Song(id=1, **{a: f"κείμενο {a}" for a in SONGS.searchable_fields}))
    db.session.commit()

    text = _text_of(app.test_client().get(
        "/api/replace/preview?q=κείμενο&ds=songs&id=1").get_json()["html"])
    for attr in stacked:
        assert f"κείμενο {attr}" in text, f"stacked field {attr} missing from preview"


def test_preview_box_height_follows_the_field_rows(app):
    """Each box carries its field's row count, so it keeps the card's shape."""
    from datasets import SONGS

    db.session.add(Song(id=1, title="Νοσταλγία", lyrics="στίχοι"))
    db.session.commit()
    html = app.test_client().get(
        "/api/replace/preview?q=Νοσταλγία&ds=songs&id=1").get_json()["html"]

    rows = {f.attr: f.rows
            for row in SONGS.layout
            for cell in row
            for f in (cell.fields if getattr(cell, "is_stack", False) else (cell,))}
    assert f'style="--rows: {rows["lyrics"]}"' in html
    assert f'style="--rows: {rows["title"]}"' in html
