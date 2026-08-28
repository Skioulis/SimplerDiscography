"""Full-text index upkeep.

Each archive's FTS5 table is external-content and maintained by AFTER
INSERT/UPDATE/DELETE triggers. If a trigger is missing the index silently
drifts from the table, so these assert the index after each kind of write.
"""

import pytest
from sqlalchemy import text

from datasets import BIOS, DATASET_LIST, DISC45, DISC78, SONGS
from extensions import db
from models import Biography, Disc45, Disc78, Song


def _fts_count(dataset):
    return db.session.execute(
        text(f"SELECT COUNT(*) FROM {dataset.fts_table}")).scalar_one()


def _fts_match(dataset, query):
    return db.session.execute(
        text(f"SELECT COUNT(*) FROM {dataset.fts_table} "
             f"WHERE {dataset.fts_table} MATCH :q"),
        {"q": query},
    ).scalar_one()


@pytest.mark.parametrize("dataset", DATASET_LIST, ids=lambda d: d.key)
def test_every_archive_has_an_empty_index(app, dataset):
    assert _fts_count(dataset) == 0


def test_insert_is_indexed(app):
    db.session.add(Disc45(id=1, title="ΝΟΣΤΑΛΓΙΑ", creators="Δερβενιώτης"))
    db.session.commit()
    assert _fts_count(DISC45) == 1
    assert _fts_match(DISC45, "νοσταλγια*") == 1


def test_update_reindexes(app):
    disc = Disc78(id=1, title="ΠΑΛΙΟΣ ΤΙΤΛΟΣ", genre="Ρεμπέτικο")
    db.session.add(disc)
    db.session.commit()

    disc.title = "ΝΕΟΣ ΤΙΤΛΟΣ"
    db.session.commit()

    assert _fts_count(DISC78) == 1                  # not duplicated
    assert _fts_match(DISC78, "παλιος*") == 0       # old text gone
    assert _fts_match(DISC78, "νεος*") == 1         # new text present


def test_delete_removes_from_index(app):
    bio = Biography(name="Τσιτσάνης Βασίλης", capacity="Συνθέτης")
    db.session.add(bio)
    db.session.commit()
    assert _fts_match(BIOS, "τσιτσανης*") == 1

    db.session.delete(bio)
    db.session.commit()
    assert _fts_count(BIOS) == 0
    assert _fts_match(BIOS, "τσιτσανης*") == 0


def test_search_is_accent_insensitive(app):
    db.session.add(Song(title="Νοσταλγία", composer="Δερβενιώτης"))
    db.session.commit()
    assert _fts_match(SONGS, "νοσταλγια*") == 1


def test_micro_sign_and_greek_mu_match_the_same_query(app):
    """Both archive spellings of Ρεμπέτικο must answer one query."""
    db.session.add_all([
        Disc78(id=1, title="Α", genre="Ρεµπέτικο"),   # U+00B5 MICRO SIGN
        Disc78(id=2, title="Β", genre="Ρεμπέτικο"),   # U+03BC GREEK MU
    ])
    db.session.commit()
    assert _fts_match(DISC78, "ρεμπετικο*") == 2


def test_archives_are_indexed_independently(app):
    """A write to one archive must not appear in another archive's index."""
    db.session.add(Disc45(id=1, title="ΜΟΝΟ ΣΤΑ 45"))
    db.session.commit()
    assert _fts_match(DISC45, "μονο*") == 1
    assert _fts_count(DISC78) == 0
    assert _fts_count(SONGS) == 0
    assert _fts_count(BIOS) == 0
