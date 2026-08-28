"""Dashboard statistics computed from the archive tables.

Kept separate from the views so the queries can be tested and reused.
:func:`dashboard_stats` takes a dataset and dispatches on its ``stats_kind``:

    songs — the original catalogue: decades and formats mined from free text
    disc  — the ΜΑΝΙΑΤΗ 45/78 archives: decades from ΕΤΟΣ, labels, genres
    bios  — Βιογραφίες: breakdown by ΙΔΙΟΤΗΤΑ
"""

from __future__ import annotations

import datetime
import re
from collections import Counter

from sqlalchemy import func, select

from datasets import Dataset
from extensions import db
from models import Song

# Values that stand in for "no named person" rather than a real creator.
# Excluded from the top composers/lyricists rankings.
PLACEHOLDER_NAMES = {
    "", "-", "--", "//", "-//-", ".", "…",
    "παραδοσιακό", "Παραδοσιακό", "παραδοσιακ.",
    "άγνωστος", "Άγνωστος", "άγνωστο", "Άγνωστο", "αγνώστου",
}

# Recording-era detection: 4-digit years read from free text. Capped at the
# current year so catalog numbers that look like future years don't leak in.
YEAR_RE = re.compile(r"\b(1[89]\d\d|20[0-2]\d)\b")
YEAR_MIN, YEAR_MAX = 1900, datetime.date.today().year

# Physical release formats, matched as substrings in the same free text.
FORMAT_TAGS = ["45άρι", "78άρι", "LP", "EP", "CD", "33άρι"]

# Fields shown in the coverage panel of the songs dashboard, in order.
SONG_COVERAGE_FIELDS = (
    "title", "composer", "lyricist", "lyrics", "archive", "notes", "bibliography",
)

DECADE_RANGE = range(1900, 2030, 10)


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #

def _total(dataset: Dataset) -> int:
    return db.session.scalar(select(func.count()).select_from(dataset.model)) or 0


# U+00B5 MICRO SIGN and U+03BC GREEK SMALL LETTER MU are mixed throughout the
# archives, so "Ρεµπέτικο" and "Ρεμπέτικο" are different strings on disk. Search
# handles this by folding (see models.fold), but counts and rankings group on the
# stored value, which would list — and rank — each spelling separately.
MICRO_SIGN = "\u00b5"
GREEK_MU = "\u03bc"


def _grouping_key(column):
    """The column normalized for grouping, so mu spellings count as one value.

    Stored values are left byte-exact; only the grouping key is normalized. The
    normalized spelling (Greek mu) is what gets displayed.
    """
    return func.replace(column, MICRO_SIGN, GREEK_MU)


def _top_names(column, limit: int = 10) -> list[dict]:
    """Return the most frequent real names in a column (placeholders removed)."""
    key = _grouping_key(column)
    rows = db.session.execute(
        select(key, func.count().label("n"))
        .where(column.notin_(PLACEHOLDER_NAMES))
        .group_by(key)
        .order_by(func.count().desc())
        .limit(limit)
    ).all()
    return [{"name": name, "count": n} for name, n in rows]


def _distinct(column) -> int:
    return db.session.scalar(
        select(func.count(func.distinct(_grouping_key(column))))
        .where(func.trim(column) != "")
    ) or 0


def _coverage(dataset: Dataset, fields: tuple[str, ...]) -> list[dict]:
    """Percentage of records with a non-empty value, per field."""
    model = dataset.model
    total = _total(dataset)
    out = []
    for attr in fields:
        col = getattr(model, attr)
        filled = db.session.scalar(
            select(func.count()).select_from(model).where(func.trim(col) != "")
        ) or 0
        out.append({
            "field": attr,
            "label": dataset.label(attr),
            "filled": filled,
            "pct": round(100 * filled / total, 1) if total else 0.0,
        })
    return out


def _decade_list(decades: Counter[int]) -> list[dict]:
    return [
        {"decade": d, "label": f"{d}s", "count": decades.get(d, 0)}
        for d in DECADE_RANGE
    ]


def _period(year_min: int | None, year_max: int | None) -> str:
    return f"{year_min}–{year_max}" if year_min and year_max else "—"


# --------------------------------------------------------------------------- #
# Τραγούδια
# --------------------------------------------------------------------------- #

def _song_eras_and_formats() -> dict:
    """Single scan over archive+notes for recording decades and formats."""
    rows = db.session.execute(select(Song.archive, Song.notes)).all()

    decades: Counter[int] = Counter()
    formats: Counter[str] = Counter()
    dated = undated = 0
    overall_min = overall_max = None

    for archive, notes in rows:
        blob = f"{archive}\n{notes}"
        years = [int(y) for y in YEAR_RE.findall(blob) if YEAR_MIN <= int(y) <= YEAR_MAX]
        if years:
            dated += 1
            # Bucket a song by the earliest year it references (its likely origin).
            decades[(min(years) // 10) * 10] += 1
            lo, hi = min(years), max(years)
            overall_min = lo if overall_min is None else min(overall_min, lo)
            overall_max = hi if overall_max is None else max(overall_max, hi)
        else:
            undated += 1
        for tag in FORMAT_TAGS:
            if tag in blob:
                formats[tag] += 1

    return {
        "decades": _decade_list(decades),
        "formats": [
            {"tag": tag, "count": formats.get(tag, 0)}
            for tag in sorted(FORMAT_TAGS, key=lambda t: formats.get(t, 0), reverse=True)
        ],
        "dated": dated,
        "undated": undated,
        "year_min": overall_min,
        "year_max": overall_max,
    }


def _songs_stats(dataset: Dataset) -> dict:
    era = _song_eras_and_formats()
    return {
        "total": _total(dataset),
        "composers": _distinct(Song.composer),
        "lyricists": _distinct(Song.lyricist),
        "period": _period(era["year_min"], era["year_max"]),
        "dated": era["dated"],
        "undated": era["undated"],
        "decades": era["decades"],
        "formats": era["formats"],
        "top_composers": _top_names(Song.composer),
        "top_lyricists": _top_names(Song.lyricist),
        "coverage": _coverage(dataset, SONG_COVERAGE_FIELDS),
    }


# --------------------------------------------------------------------------- #
# 45άρια / 78άρια
# --------------------------------------------------------------------------- #

def _disc_stats(dataset: Dataset) -> dict:
    """Stats for a ΜΑΝΙΑΤΗ disc archive.

    ΕΤΟΣ is free text, so years are parsed rather than read: a record counts as
    dated when a plausible 4-digit year can be found in the field.
    """
    model = dataset.model
    years = db.session.execute(select(model.year)).scalars().all()

    decades: Counter[int] = Counter()
    dated = undated = 0
    overall_min = overall_max = None
    for value in years:
        found = [int(y) for y in YEAR_RE.findall(value or "")
                 if YEAR_MIN <= int(y) <= YEAR_MAX]
        if found:
            dated += 1
            earliest = min(found)
            decades[(earliest // 10) * 10] += 1
            overall_min = earliest if overall_min is None else min(overall_min, earliest)
            hi = max(found)
            overall_max = hi if overall_max is None else max(overall_max, hi)
        else:
            undated += 1

    return {
        "total": _total(dataset),
        "companies": _distinct(model.company),
        "discs": _distinct(model.disc_number),
        "period": _period(overall_min, overall_max),
        "dated": dated,
        "undated": undated,
        "decades": _decade_list(decades),
        "top_companies": _top_names(model.company),
        "top_genres": _top_names(model.genre),
        "top_rhythms": _top_names(model.rhythm),
        "coverage": _coverage(dataset, dataset.searchable_fields),
    }


# --------------------------------------------------------------------------- #
# Βιογραφίες
# --------------------------------------------------------------------------- #

def _bios_stats(dataset: Dataset) -> dict:
    model = dataset.model
    return {
        "total": _total(dataset),
        "capacities": _distinct(model.capacity),
        "with_discography": db.session.scalar(
            select(func.count()).select_from(model)
            .where(func.trim(model.discography) != "")
        ) or 0,
        "top_capacities": _top_names(model.capacity, limit=12),
        "coverage": _coverage(dataset, dataset.searchable_fields),
    }


_BUILDERS = {
    "songs": _songs_stats,
    "disc": _disc_stats,
    "bios": _bios_stats,
}


def dashboard_stats(dataset: Dataset) -> dict:
    """Assemble everything the dataset's dashboard template needs."""
    return _BUILDERS[dataset.stats_kind](dataset)
