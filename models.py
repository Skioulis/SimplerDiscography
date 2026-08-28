"""Data models for the archives.

The application holds four independent archives, each backed by one table:

    Song      — files/Τραγούδια.csv          (the original catalogue)
    Disc45    — files/45άρια (ΜΑΝΙΑΤΗ).csv
    Disc78    — files/78άρια (ΜΑΝΙΑΤΗ).csv
    Biography — files/Βιογραφίες.csv

Attributes use English snake_case names; the original Greek headers live in each
model's ``CSV_COLUMNS`` / ``LABELS`` so the UI can render them without
hardcoding labels. ``Disc45`` and ``Disc78`` share a schema but stay separate
tables, so either can diverge later without conditionals.

``ArchiveRecord`` carries what every archive needs: the derived search blob and
the created/updated timestamps.

Full-text search is accent-insensitive. SQLite's FTS5 tokenizer does not strip
Greek accents (tonos), so we fold text ourselves: ``fold()`` removes diacritics
and lowercases, and the folded concatenation of all searchable fields is stored
in the derived ``search_blob`` column, which an FTS5 table covers. Queries are
folded the same way before matching. ``search_blob`` is kept in sync
automatically via ORM ``before_insert`` / ``before_update`` events.
"""

from __future__ import annotations

import unicodedata
from datetime import datetime

from sqlalchemy import DateTime, Text, event, func, text
from sqlalchemy.orm import Mapped, mapped_column

from extensions import db

# Baseline timestamp for records that predate change-tracking: the existing
# catalogue is seeded with this value, and it is the column default.
DEFAULT_TIMESTAMP = datetime(2026, 1, 1, 0, 0, 0)
_DEFAULT_TIMESTAMP_SQL = "'2026-01-01 00:00:00'"

# Header of the source id column in the 45άρια / 78άρια exports.
CSV_ID_HEADER = "Αναγνωριστικό"


def fold(s: str | None) -> str:
    """Normalize text for accent-insensitive search.

    Decomposes characters compatibly, drops combining marks (Greek tonos, Latin
    accents), and lowercases. e.g. "Νοσταλγία" -> "νοσταλγια".

    NFKD rather than NFD is deliberate: the source archives mix U+00B5 MICRO
    SIGN with U+03BC GREEK SMALL LETTER MU, so "Ρεµπέτικο" and "Ρεμπέτικο" are
    different strings on disk. NFKD folds the micro sign to mu, and the two
    spellings then match the same query.
    """
    if not s:
        return ""
    decomposed = unicodedata.normalize("NFKD", s)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return stripped.lower()


class ArchiveRecord:
    """Mixin shared by every archive table.

    Subclasses must define ``CSV_COLUMNS``, ``LABELS`` and ``SEARCHABLE_FIELDS``.
    """

    # Derived: folded (accent-stripped, lowercased) concatenation of all
    # searchable fields. Not shown to users; maintained by _sync_search_blob
    # and indexed by the table's FTS5 companion.
    search_blob: Mapped[str] = mapped_column(Text, default="")

    # Change-tracking. Both default to DEFAULT_TIMESTAMP (2026-01-01 00:00:00);
    # `updated` also bumps to the current time whenever a row is modified.
    created: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=DEFAULT_TIMESTAMP,
        server_default=text(_DEFAULT_TIMESTAMP_SQL),
    )
    updated: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=DEFAULT_TIMESTAMP,
        server_default=text(_DEFAULT_TIMESTAMP_SQL),
        onupdate=func.now(),
    )

    # Header of the id column in the source CSV, or None when the CSV carries no
    # id and rows are numbered in file order.
    CSV_ID_COLUMN: str | None = None

    # Set by subclasses.
    CSV_COLUMNS: dict[str, str] = {}
    LABELS: dict[str, str] = {}
    SEARCHABLE_FIELDS: tuple[str, ...] = ()

    @classmethod
    def build_search_blob(cls, values) -> str:
        """Build the folded search blob from a mapping or object of fields."""
        get = values.get if isinstance(values, dict) else lambda k: getattr(values, k, "")
        return fold(" ".join(get(f) or "" for f in cls.SEARCHABLE_FIELDS))

    def to_dict(self) -> dict[str, object]:
        """Return the record as a plain dict keyed by attribute name."""
        return {
            "id": self.id,
            **{attr: getattr(self, attr) for attr in self.SEARCHABLE_FIELDS},
        }

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        label = getattr(self, self.SEARCHABLE_FIELDS[0], "")
        return f"<{type(self).__name__} id={self.id} {self.SEARCHABLE_FIELDS[0]}={label!r}>"


class Song(ArchiveRecord, db.Model):
    """The original Τραγούδια catalogue."""

    __tablename__ = "song"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    title: Mapped[str] = mapped_column(Text, index=True, default="")
    composer: Mapped[str] = mapped_column(Text, index=True, default="")
    lyricist: Mapped[str] = mapped_column(Text, index=True, default="")
    lyrics: Mapped[str] = mapped_column(Text, default="")
    archive: Mapped[str] = mapped_column(Text, default="")
    bibliography: Mapped[str] = mapped_column(Text, default="")
    notes: Mapped[str] = mapped_column(Text, default="")

    # Maps CSV column header (Greek) -> model attribute name.
    # Order matches the columns in the source CSV.
    CSV_COLUMNS: dict[str, str] = {
        "ΤΙΤΛΟΣ": "title",
        "ΣΥΝΘΕΤΗΣ": "composer",
        "ΣΤΙΧΟΥΡΓΟΣ": "lyricist",
        "ΣΤΙΧΟΙ": "lyrics",
        "ΑΡΧΕΙΟ": "archive",
        "ΒΙΒΛΙΟΓΡΑΦΙΑ": "bibliography",
        "ΣΗΜΕΙΩΣΕΙΣ": "notes",
    }

    # Maps model attribute name -> Greek display label (for the UI).
    LABELS: dict[str, str] = {attr: header for header, attr in CSV_COLUMNS.items()}

    # Fields that feed the full-text index, in display order.
    SEARCHABLE_FIELDS: tuple[str, ...] = (
        "title",
        "composer",
        "lyricist",
        "lyrics",
        "archive",
        "bibliography",
        "notes",
    )


class _DiscRecord(ArchiveRecord):
    """Shared shape of the ΜΑΝΙΑΤΗ 45 and 78 rpm archives.

    Both exports have identical columns. They stay separate tables (separate
    subclasses below) so either can gain fields later without touching the
    other; only the column definitions and the CSV mapping are shared here.

    ``id`` comes from the source ``Αναγνωριστικό`` column rather than being
    autoincremented: the exports are numbered with gaps (the 45s run 1–38947
    over 37,750 rows), and preserving the numbering keeps prev/next navigation
    aligned with the original archive.
    """

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)

    company: Mapped[str] = mapped_column(Text, index=True, default="")
    disc_number: Mapped[str] = mapped_column(Text, index=True, default="")
    title: Mapped[str] = mapped_column(Text, index=True, default="")
    creators: Mapped[str] = mapped_column(Text, index=True, default="")
    # Free text, not an integer: the field is unparseable in places and only
    # 62.8% filled on the 78s.
    year: Mapped[str] = mapped_column(Text, default="")
    genre: Mapped[str] = mapped_column(Text, index=True, default="")
    rhythm: Mapped[str] = mapped_column(Text, index=True, default="")

    CSV_ID_COLUMN = CSV_ID_HEADER

    CSV_COLUMNS: dict[str, str] = {
        "ΕΤΑΙΡΕΙΑ": "company",
        "ΑΡΙΘΜΟΣ ΔΙΣΚΟΥ": "disc_number",
        "ΤΙΤΛΟΣ ΤΡΑΓΟΥΔΙΟΥ": "title",
        "ΤΡΑΓΟΥΔ / ΣΥΝΘΕΤ / ΣΤΙΧΟΥ": "creators",
        "ΕΤΟΣ": "year",
        "ΕΙΔΟΣ": "genre",
        "ΡΥΘΜΟΣ": "rhythm",
    }

    SEARCHABLE_FIELDS: tuple[str, ...] = (
        "title",
        "creators",
        "company",
        "disc_number",
        "year",
        "genre",
        "rhythm",
    )


class Disc45(_DiscRecord, db.Model):
    """45άρια (ΜΑΝΙΑΤΗ) — 45 rpm singles."""

    __tablename__ = "disc45"

    # The 45άρια form labels the creators column ΔΗΜΙΟΥΡΓΟΙ.
    LABELS: dict[str, str] = {
        "company": "ΕΤΑΙΡΕΙΑ",
        "disc_number": "ΑΡΙΘΜΟΣ ΔΙΣΚΟΥ",
        "title": "ΤΙΤΛΟΣ ΤΡΑΓΟΥΔΙΟΥ",
        "creators": "ΔΗΜΙΟΥΡΓΟΙ",
        "year": "ΕΤΟΣ",
        "genre": "ΕΙΔΟΣ",
        "rhythm": "ΡΥΘΜΟΣ",
    }


class Disc78(_DiscRecord, db.Model):
    """78άρια (ΜΑΝΙΑΤΗ) — 78 rpm discs."""

    __tablename__ = "disc78"

    # The 78άρια form labels the same column ΤΡΑΓΟΥΔ / ΣΥΝΘΕΤ / ΣΤΙΧ.
    LABELS: dict[str, str] = {
        "company": "ΕΤΑΙΡΕΙΑ",
        "disc_number": "ΑΡΙΘΜΟΣ ΔΙΣΚΟΥ",
        "title": "ΤΙΤΛΟΣ ΤΡΑΓΟΥΔΙΟΥ",
        "creators": "ΤΡΑΓΟΥΔ / ΣΥΝΘΕΤ / ΣΤΙΧ",
        "year": "ΕΤΟΣ",
        "genre": "ΕΙΔΟΣ",
        "rhythm": "ΡΥΘΜΟΣ",
    }


class Biography(ArchiveRecord, db.Model):
    """Βιογραφίες — performer, composer and lyricist biographies.

    The export carries no id column, so rows are numbered in file order.
    """

    __tablename__ = "biography"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    name: Mapped[str] = mapped_column(Text, index=True, default="")
    place_dates: Mapped[str] = mapped_column(Text, default="")
    capacity: Mapped[str] = mapped_column(Text, index=True, default="")
    details: Mapped[str] = mapped_column(Text, default="")
    discography: Mapped[str] = mapped_column(Text, default="")

    CSV_COLUMNS: dict[str, str] = {
        "ΟΝΟΜΑΤΕΠΩΝΥΜΟ": "name",
        "ΤΟΠΟΣ-ΧΡΟΝΟΛΟΓΙΕΣ": "place_dates",
        "ΙΔΙΟΤΗΤΑ": "capacity",
        "ΣΤΟΙΧΕΙΑ": "details",
        "ΔΙΣΚΟΓΡΑΦΙΑ": "discography",
    }

    LABELS: dict[str, str] = {attr: header for header, attr in CSV_COLUMNS.items()}

    SEARCHABLE_FIELDS: tuple[str, ...] = (
        "name",
        "capacity",
        "place_dates",
        "details",
        "discography",
    )


#: Every concrete archive model, in presentation order.
ARCHIVE_MODELS: tuple[type[ArchiveRecord], ...] = (Song, Disc45, Disc78, Biography)


def _sync_search_blob(mapper, connection, target) -> None:
    """Keep search_blob current whenever a record is written via the ORM."""
    target.search_blob = type(target).build_search_blob(target)


for _model in ARCHIVE_MODELS:
    event.listen(_model, "before_insert", _sync_search_blob)
    event.listen(_model, "before_update", _sync_search_blob)
