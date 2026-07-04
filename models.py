"""Data model for the discography.

A single ``Song`` table mirrors the seven columns of ``files/Τραγούδια.csv``.
Attributes use English snake_case names; the original Greek headers live in
``Song.LABELS`` so the UI can render them without hardcoding labels.

``id`` is an autoincrement primary key assigned in CSV row order, so
prev/next navigation follows the original sequence of the source file.

Full-text search is accent-insensitive. SQLite's FTS5 tokenizer does not strip
Greek accents (tonos), so we fold text ourselves: ``fold()`` removes diacritics
and lowercases, and the folded concatenation of all searchable fields is stored
in the derived ``search_blob`` column, which the FTS5 index covers. Queries are
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


def fold(s: str | None) -> str:
    """Normalize text for accent-insensitive search.

    Decomposes characters, drops combining marks (Greek tonos, Latin accents),
    and lowercases. e.g. "Νοσταλγία" -> "νοσταλγια".
    """
    if not s:
        return ""
    decomposed = unicodedata.normalize("NFD", s)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return stripped.lower()


class Song(db.Model):
    __tablename__ = "song"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    title: Mapped[str] = mapped_column(Text, index=True, default="")
    composer: Mapped[str] = mapped_column(Text, index=True, default="")
    lyricist: Mapped[str] = mapped_column(Text, index=True, default="")
    lyrics: Mapped[str] = mapped_column(Text, default="")
    archive: Mapped[str] = mapped_column(Text, default="")
    bibliography: Mapped[str] = mapped_column(Text, default="")
    notes: Mapped[str] = mapped_column(Text, default="")

    # Derived: folded (accent-stripped, lowercased) concatenation of all
    # searchable fields. Not shown to users; maintained by the events below and
    # indexed by the song_fts FTS5 table.
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

    @classmethod
    def build_search_blob(cls, values) -> str:
        """Build the folded search blob from a mapping or object of fields."""
        get = values.get if isinstance(values, dict) else lambda k: getattr(values, k, "")
        return fold(" ".join(get(f) or "" for f in cls.SEARCHABLE_FIELDS))

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Song id={self.id} title={self.title!r}>"

    def to_dict(self) -> dict[str, object]:
        """Return the record as a plain dict keyed by attribute name."""
        return {
            "id": self.id,
            **{attr: getattr(self, attr) for attr in self.SEARCHABLE_FIELDS},
        }


@event.listens_for(Song, "before_insert")
@event.listens_for(Song, "before_update")
def _sync_search_blob(mapper, connection, target: Song) -> None:
    """Keep search_blob current whenever a Song is written via the ORM."""
    target.search_blob = Song.build_search_blob(target)
