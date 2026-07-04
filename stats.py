"""Dashboard statistics computed from the discography database.

Kept separate from the view so the queries can be tested and reused. All
figures are derived from the current contents of the ``song`` table.
"""

from __future__ import annotations

import datetime
import re
from collections import Counter

from sqlalchemy import func, select

from extensions import db
from models import Song

# Values that stand in for "no named person" rather than a real creator.
# Excluded from the top composers/lyricists rankings.
PLACEHOLDER_NAMES = {
    "", "-", "--", "//", "-//-", ".", "…",
    "παραδοσιακό", "Παραδοσιακό", "παραδοσιακ.",
    "άγνωστος", "Άγνωστος", "άγνωστο", "Άγνωστο", "αγνώστου",
}

# Recording-era detection: 4-digit years read from the free-text ΑΡΧΕΙΟ and
# ΣΗΜΕΙΩΣΕΙΣ fields. Capped at the current year so catalog numbers that look
# like future years don't leak into the range.
YEAR_RE = re.compile(r"\b(1[89]\d\d|20[0-2]\d)\b")
YEAR_MIN, YEAR_MAX = 1900, datetime.date.today().year

# Physical release formats, matched as substrings in the same free text.
FORMAT_TAGS = ["45άρι", "78άρι", "LP", "EP", "CD", "33άρι"]

# Fields shown in the coverage panel, with their Greek labels.
COVERAGE_FIELDS = ("title", "composer", "lyricist", "lyrics", "archive", "notes", "bibliography")


def _top_names(column, limit: int = 10) -> list[dict]:
    """Return the most frequent real names in a column (placeholders removed)."""
    rows = db.session.execute(
        select(column, func.count().label("n"))
        .where(column.notin_(PLACEHOLDER_NAMES))
        .group_by(column)
        .order_by(func.count().desc())
        .limit(limit)
    ).all()
    return [{"name": name, "count": n} for name, n in rows]


def _coverage() -> list[dict]:
    """Percentage of records with a non-empty value, per field."""
    total = db.session.scalar(select(func.count()).select_from(Song)) or 0
    out = []
    for attr in COVERAGE_FIELDS:
        col = getattr(Song, attr)
        filled = db.session.scalar(
            select(func.count()).select_from(Song).where(func.trim(col) != "")
        ) or 0
        pct = round(100 * filled / total, 1) if total else 0.0
        out.append({
            "field": attr,
            "label": Song.LABELS[attr],
            "filled": filled,
            "pct": pct,
        })
    return out


def _eras_and_formats() -> dict:
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

    decade_list = [
        {"decade": d, "label": f"{d}s", "count": decades.get(d, 0)}
        for d in range(1900, 2030, 10)
    ]
    format_list = [
        {"tag": tag, "count": formats.get(tag, 0)}
        for tag in sorted(FORMAT_TAGS, key=lambda t: formats.get(t, 0), reverse=True)
    ]
    return {
        "decades": decade_list,
        "formats": format_list,
        "dated": dated,
        "undated": undated,
        "year_min": overall_min,
        "year_max": overall_max,
    }


def dashboard_stats() -> dict:
    """Assemble everything the dashboard template needs."""
    total = db.session.scalar(select(func.count()).select_from(Song)) or 0
    composers = db.session.scalar(
        select(func.count(func.distinct(Song.composer))).where(func.trim(Song.composer) != "")
    ) or 0
    lyricists = db.session.scalar(
        select(func.count(func.distinct(Song.lyricist))).where(func.trim(Song.lyricist) != "")
    ) or 0

    era = _eras_and_formats()
    if era["year_min"] and era["year_max"]:
        period = f"{era['year_min']}–{era['year_max']}"
    else:
        period = "—"

    return {
        "total": total,
        "composers": composers,
        "lyricists": lyricists,
        "period": period,
        "dated": era["dated"],
        "undated": era["undated"],
        "decades": era["decades"],
        "formats": era["formats"],
        "top_composers": _top_names(Song.composer),
        "top_lyricists": _top_names(Song.lyricist),
        "coverage": _coverage(),
    }
