"""The dataset registry.

Each archive is described once, here, by a :class:`Dataset` descriptor. The
views, templates, importers and statistics all read the registry rather than
naming tables directly, so the four archives share one set of routes and one
record template instead of four near-copies.

Adding a fifth archive means adding a model in ``models.py``, a migration, and
one entry in ``DATASETS`` below.
"""

from __future__ import annotations

from dataclasses import dataclass, field as dc_field

from models import Biography, Disc45, Disc78, Song


@dataclass(frozen=True)
class Field:
    """One field in the record card.

    ``width`` is a Bootstrap column span (out of 12), ignored inside a
    :class:`Stack`, which sets the width for the whole column. ``rows`` and
    ``grow`` apply only to textareas: ``rows`` is the natural height, ``grow``
    lets the box absorb whatever height the neighbouring column adds.
    """

    attr: str
    widget: str = "input"  # "input" | "textarea"
    rows: int = 1
    width: int = 4
    grow: bool = False

    is_stack = False


@dataclass(frozen=True)
class Stack:
    """Several fields stacked vertically inside one column of a row.

    A plain row puts one field per column, so a column with a short field
    beside a tall one is left with dead space under it. A ``Stack`` fills that
    column top to bottom instead; mark the field that should soak up the
    leftover height with ``grow=True``.
    """

    width: int
    fields: tuple[Field, ...]

    is_stack = True


@dataclass(frozen=True)
class Dataset:
    """Everything the app needs to know about one archive."""

    key: str  # URL slug and registry key
    model: type
    title: str  # sidebar entry and page heading
    record_heading: str  # heading on the record card
    new_heading: str  # heading on the add-new form
    delete_noun: str  # "αυτό το τραγούδι" etc., for the delete dialog
    theme: str  # CSS accent class on <body>
    csv_filename: str
    fts_table: str
    stats_kind: str  # which dashboard builder/template to use
    layout: tuple[tuple[Field | Stack, ...], ...]  # rows of cells
    result_title: str  # attribute shown as the result headline
    result_meta: tuple[str, ...]  # attributes shown beneath it

    @property
    def labels(self) -> dict[str, str]:
        return self.model.LABELS

    @property
    def searchable_fields(self) -> tuple[str, ...]:
        return self.model.SEARCHABLE_FIELDS

    @property
    def field_options(self) -> list[tuple[str, str]]:
        """(value, label) pairs for the search/replace scope dropdowns."""
        return [(f, self.model.LABELS[f]) for f in self.model.SEARCHABLE_FIELDS]

    def label(self, attr: str) -> str:
        return self.model.LABELS.get(attr, attr)


# --------------------------------------------------------------------------- #
# Layouts
#
# Field order follows the original Access forms exactly; only the arrangement
# into rows and columns is adapted to the responsive card.
# --------------------------------------------------------------------------- #

# The body is one row of two stacked columns rather than two rows, so that
# ΣΗΜΕΙΩΣΕΙΣ can grow into the space ΑΡΧΕΙΟ leaves under itself instead of the
# card carrying a hole there. Field order is the original one: ΣΤΙΧΟΙ and
# ΒΙΒΛΙΟΓΡΑΦΙΑ down the left, ΑΡΧΕΙΟ then ΣΗΜΕΙΩΣΕΙΣ down the right.
_SONG_LAYOUT = (
    (
        Field("title", width=4),
        Field("composer", width=4),
        Field("lyricist", width=4),
    ),
    (
        Stack(
            4,
            (
                Field("lyrics", "textarea", rows=14),
                Field("bibliography", "textarea", rows=4),
            ),
        ),
        Stack(
            8,
            (
                Field("archive", "textarea", rows=4),
                Field("notes", "textarea", rows=14, grow=True),
            ),
        ),
    ),
)

# ΕΤΑΙΡΕΙΑ, ΑΡΙΘΜΟΣ ΔΙΣΚΟΥ, ΤΙΤΛΟΣ, ΔΗΜΙΟΥΡΓΟΙ, ΕΤΟΣ, ΕΙΔΟΣ, ΡΥΘΜΟΣ —
# the reading order of both ΜΑΝΙΑΤΗ forms.
_DISC_LAYOUT = (
    (
        Field("company", "textarea", rows=2, width=6),
        Field("disc_number", "textarea", rows=2, width=6),
    ),
    (Field("title", "textarea", rows=3, width=12),),
    (Field("creators", "textarea", rows=3, width=12),),
    (
        Field("year", width=4),
        Field("genre", width=4),
        Field("rhythm", width=4),
    ),
)

# ΟΝΟΜΑΤΕΠΩΝΥΜΟ / ΤΟΠΟΣ-ΧΡΟΝΟΛΟΓΙΕΣ / ΙΔΙΟΤΗΤΑ across the top, then the wide
# ΣΤΟΙΧΕΙΑ body with ΔΙΣΚΟΓΡΑΦΙΑ beside it, as in the Βιογραφίες form.
_BIO_LAYOUT = (
    (
        Field("name", width=4),
        Field("place_dates", width=4),
        Field("capacity", width=4),
    ),
    (
        Field("details", "textarea", rows=18, width=8),
        Field("discography", "textarea", rows=18, width=4),
    ),
)


# --------------------------------------------------------------------------- #
# The registry
# --------------------------------------------------------------------------- #

SONGS = Dataset(
    key="songs",
    model=Song,
    title="Τραγούδια",
    record_heading="Στοιχεία τραγουδιού",
    new_heading="Νέο τραγούδι",
    delete_noun="αυτή την εγγραφή",
    theme="theme-songs",
    csv_filename="Τραγούδια.csv",
    fts_table="song_fts",
    stats_kind="songs",
    layout=_SONG_LAYOUT,
    result_title="title",
    result_meta=("composer", "lyricist"),
)

DISC45 = Dataset(
    key="45",
    model=Disc45,
    title="45άρια (ΜΑΝΙΑΤΗ)",
    record_heading="Στοιχεία δίσκου 45 στροφών",
    new_heading="Νέο 45άρι",
    delete_noun="αυτόν τον δίσκο",
    theme="theme-disc45",
    csv_filename="45άρια (ΜΑΝΙΑΤΗ).csv",
    fts_table="disc45_fts",
    stats_kind="disc",
    layout=_DISC_LAYOUT,
    result_title="title",
    result_meta=("creators", "company"),
)

DISC78 = Dataset(
    key="78",
    model=Disc78,
    title="78άρια (ΜΑΝΙΑΤΗ)",
    record_heading="Στοιχεία δίσκου 78 στροφών",
    new_heading="Νέο 78άρι",
    delete_noun="αυτόν τον δίσκο",
    theme="theme-disc78",
    csv_filename="78άρια (ΜΑΝΙΑΤΗ).csv",
    fts_table="disc78_fts",
    stats_kind="disc",
    layout=_DISC_LAYOUT,
    result_title="title",
    result_meta=("creators", "company"),
)

BIOS = Dataset(
    key="bios",
    model=Biography,
    title="Βιογραφίες",
    record_heading="Στοιχεία βιογραφίας",
    new_heading="Νέα βιογραφία",
    delete_noun="αυτή τη βιογραφία",
    theme="theme-bios",
    csv_filename="Βιογραφίες.csv",
    fts_table="bio_fts",
    stats_kind="bios",
    layout=_BIO_LAYOUT,
    result_title="name",
    result_meta=("capacity", "place_dates"),
)

#: Registry, in sidebar order.
DATASET_LIST: tuple[Dataset, ...] = (SONGS, DISC45, DISC78, BIOS)

#: Slug -> Dataset.
DATASETS: dict[str, Dataset] = {d.key: d for d in DATASET_LIST}

#: The dataset served by the legacy, unprefixed routes.
DEFAULT_DATASET = SONGS

#: Accepted slugs, for the URL converter.
DATASET_KEYS: tuple[str, ...] = tuple(DATASETS)


def get(key: str | None) -> Dataset:
    """Look up a dataset by slug, falling back to the default."""
    return DATASETS.get(key or "", DEFAULT_DATASET)


def by_model(model: type) -> Dataset:
    """Look up a dataset by its model class."""
    for d in DATASET_LIST:
        if d.model is model:
            return d
    raise KeyError(model)
