"""CSV parsing and per-archive replacement."""

import io

import pytest

from dataio import CSVFormatError, read_rows, record_count, replace_all
from datasets import BIOS, DISC45, DISC78, SONGS

DISC_HEADER = ("Αναγνωριστικό;ΕΤΑΙΡΕΙΑ;ΑΡΙΘΜΟΣ ΔΙΣΚΟΥ;ΤΙΤΛΟΣ ΤΡΑΓΟΥΔΙΟΥ;"
               "ΤΡΑΓΟΥΔ / ΣΥΝΘΕΤ / ΣΤΙΧΟΥ;ΕΤΟΣ;ΕΙΔΟΣ;ΡΥΘΜΟΣ")


def _stream(text, bom=True):
    """A CSV text stream, by default with the BOM the exports actually carry."""
    raw = ("﻿" if bom else "") + text
    # utf-8-sig is how both importers open the real files.
    return io.StringIO(raw.encode("utf-8").decode("utf-8-sig"))


def test_reads_disc_rows_with_source_id():
    rows = read_rows(DISC45, _stream(
        f"{DISC_HEADER}\n"
        "7;Adinamia Ελλάδος;AD 2;ΠΟΙΟΣ ΕΙΝΑΙ Ο ΤΡΙΤΟΣ;Λευτ. Ψιλόπουλος;1974;Λαϊκό;Τραγούδι\n"
    ))
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == 7           # the source Αναγνωριστικό, not a sequence
    assert row["company"] == "Adinamia Ελλάδος"
    assert row["title"] == "ΠΟΙΟΣ ΕΙΝΑΙ Ο ΤΡΙΤΟΣ"
    assert row["year"] == "1974"


def test_reads_quoted_multiline_field():
    """ΕΤΑΙΡΕΙΑ routinely spans two lines in the real export."""
    rows = read_rows(DISC45, _stream(
        f'{DISC_HEADER}\n'
        '1;"Adinamia Ελλάδος\nADINAMIA";AD 2;ΤΙΤΛΟΣ;ΔΗΜΙΟΥΡΓΟΣ;1974;Λαϊκό;Τραγούδι\n'
    ))
    assert rows[0]["company"] == "Adinamia Ελλάδος\nADINAMIA"


def test_biography_rows_have_no_source_id():
    rows = read_rows(BIOS, _stream(
        "ΣΤΟΙΧΕΙΑ;ΔΙΣΚΟΓΡΑΦΙΑ;ΟΝΟΜΑΤΕΠΩΝΥΜΟ;ΤΟΠΟΣ-ΧΡΟΝΟΛΟΓΙΕΣ;ΙΔΙΟΤΗΤΑ\n"
        "Βιογραφικό κείμενο;;POLL;-;Συγκρότημα\n"
    ))
    assert "id" not in rows[0]      # numbered in file order by the database
    assert rows[0]["name"] == "POLL"
    assert rows[0]["capacity"] == "Συγκρότημα"


def test_search_blob_is_folded():
    rows = read_rows(DISC78, _stream(
        f"{DISC_HEADER}\n"
        "1;Εταιρεία;Μ 1;ΤΙΤΛΟΣ;ΔΗΜΙΟΥΡΓΟΣ;1934;Ρεµπέτικο;Ζεϊµπέκικος\n"
    ))
    blob = rows[0]["search_blob"]
    # Micro sign folded to Greek mu, so a normally-typed query matches.
    assert "ρεμπετικο" in blob
    # Dialytika is a combining mark like tonos, so ϊ folds to ι as well.
    # Queries are folded the same way, so "Ζεϊμπέκικος" still matches.
    assert "ζειμπεκικος" in blob


def test_missing_column_is_rejected():
    with pytest.raises(CSVFormatError, match="ΡΥΘΜΟΣ"):
        read_rows(DISC45, _stream(
            "Αναγνωριστικό;ΕΤΑΙΡΕΙΑ;ΑΡΙΘΜΟΣ ΔΙΣΚΟΥ;ΤΙΤΛΟΣ ΤΡΑΓΟΥΔΙΟΥ;"
            "ΤΡΑΓΟΥΔ / ΣΥΝΘΕΤ / ΣΤΙΧΟΥ;ΕΤΟΣ;ΕΙΔΟΣ\n"
        ))


def test_missing_id_column_is_rejected():
    with pytest.raises(CSVFormatError, match="Αναγνωριστικό"):
        read_rows(DISC45, _stream(
            "ΕΤΑΙΡΕΙΑ;ΑΡΙΘΜΟΣ ΔΙΣΚΟΥ;ΤΙΤΛΟΣ ΤΡΑΓΟΥΔΙΟΥ;"
            "ΤΡΑΓΟΥΔ / ΣΥΝΘΕΤ / ΣΤΙΧΟΥ;ΕΤΟΣ;ΕΙΔΟΣ;ΡΥΘΜΟΣ\n"
        ))


def test_non_numeric_id_names_the_line():
    with pytest.raises(CSVFormatError, match="γραμμή 3"):
        read_rows(DISC45, _stream(
            f"{DISC_HEADER}\n"
            "1;Ε;Μ 1;Τ;Δ;1934;Λαϊκό;Τραγούδι\n"
            "χχχ;Ε;Μ 2;Τ;Δ;1935;Λαϊκό;Τραγούδι\n"
        ))


def test_replace_all_touches_only_its_own_archive(app):
    """Importing one archive must not disturb the other three."""
    disc45 = read_rows(DISC45, _stream(
        f"{DISC_HEADER}\n1;Ε;Μ 1;Τ;Δ;1970;Λαϊκό;Τραγούδι\n"))
    disc78 = read_rows(DISC78, _stream(
        f"{DISC_HEADER}\n"
        "1;Ε;Μ 1;Τ;Δ;1934;Ρεμπέτικο;Ζεϊμπέκικος\n"
        "2;Ε;Μ 2;Τ;Δ;1935;Ρεμπέτικο;Ζεϊμπέκικος\n"))
    assert replace_all(DISC45, disc45) == 1
    assert replace_all(DISC78, disc78) == 2

    # Re-importing the 45s leaves the 78s and the others alone.
    assert replace_all(DISC45, disc45) == 1
    assert record_count(DISC78) == 2
    assert record_count(BIOS) == 0
    assert record_count(SONGS) == 0
