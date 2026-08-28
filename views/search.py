"""Live search API and the full results page.

Search runs against each archive's FTS5 index over the pre-folded search_blob,
so it is accent-insensitive. A field-scoped search can't use the index (the
index covers the concatenation of every field, not each one separately), so it
falls back to a folded scan of that column.

Scope: by default only the active dataset is searched. ``scope=all`` searches
every archive and groups the results, capped per group — a cross-archive lookup
is for "where does this name appear", and the per-dataset page is one click away.
"""

from __future__ import annotations

from flask import jsonify, render_template, request, url_for
from sqlalchemy import text

import datasets as datasets_mod
from datasets import DATASET_LIST, Dataset
from extensions import db
from views import (
    LIVE_SEARCH_LIMIT,
    MIN_SEARCH_LEN,
    PAGE_SIZE,
    field_hits,
    fts_query,
    main,
    page_window,
    result_row,
)

#: Results shown per archive when searching across all of them.
CROSS_GROUP_LIMIT = 5


def _select_clause(dataset: Dataset) -> str:
    """The columns a result row needs, qualified for the join below."""
    attrs = ["id", dataset.result_title, *dataset.result_meta]
    # dict.fromkeys: de-duplicate while keeping order (result_title may repeat).
    return ", ".join(f"r.{a}" for a in dict.fromkeys(attrs))


def _fts_page(dataset: Dataset, fts: str, limit: int, offset: int = 0) -> list[dict]:
    """One page of FTS hits, ordered by relevance."""
    table = dataset.model.__tablename__
    rows = db.session.execute(
        text(
            f"SELECT {_select_clause(dataset)} "
            f"FROM {dataset.fts_table} f JOIN {table} r ON r.id = f.rowid "
            f"WHERE {dataset.fts_table} MATCH :q ORDER BY rank LIMIT :lim OFFSET :off"
        ),
        {"q": fts, "lim": limit, "off": offset},
    ).mappings().all()
    return [result_row(dataset, r) for r in rows]


def _fts_count(dataset: Dataset, fts: str) -> int:
    return db.session.execute(
        text(f"SELECT COUNT(*) FROM {dataset.fts_table} WHERE {dataset.fts_table} MATCH :q"),
        {"q": fts},
    ).scalar_one()


def _clamp_page(page: int, total: int, page_size: int) -> int:
    """Keep the requested page inside the available range."""
    total_pages = (total + page_size - 1) // page_size
    return min(page, total_pages) if total_pages else 1


def _search_page(dataset: Dataset, q: str, field: str, page: int, page_size: int):
    """Search one archive. Returns (total, rows, page) with page clamped.

    Total and page are resolved in the same pass as the rows: the field-scoped
    path has to scan the column, and scanning it twice to count and then slice
    would double the cost of every field search.
    """
    if field in dataset.searchable_fields:
        hits = field_hits(dataset, q, field)
        total = len(hits)
        page = _clamp_page(page, total, page_size)
        offset = (page - 1) * page_size
        return total, [result_row(dataset, r) for r in hits[offset:offset + page_size]], page

    fts = fts_query(q)
    if not fts:
        return 0, [], 1
    total = _fts_count(dataset, fts)
    page = _clamp_page(page, total, page_size)
    return total, _fts_page(dataset, fts, page_size, (page - 1) * page_size), page


def _search_top(dataset: Dataset, q: str, field: str, limit: int):
    """The first `limit` hits for one archive. Returns (total, rows)."""
    total, rows, _ = _search_page(dataset, q, field, 1, limit)
    return total, rows


def _with_urls(rows: list[dict]) -> list[dict]:
    for row in rows:
        row["url"] = url_for("main.record", dataset=row["dataset"], rec_id=row["id"])
    return rows


def _search_all(q: str, limit: int):
    """Search every archive. Returns (grand_total, groups)."""
    groups = []
    grand_total = 0
    for ds in DATASET_LIST:
        # Field scoping is per-dataset and the fields differ, so a cross-archive
        # search always covers every field.
        total, rows = _search_top(ds, q, "", limit)
        grand_total += total
        if total:
            groups.append({
                "dataset": ds.key,
                "title": ds.title,
                "theme": ds.theme,
                "total": total,
                "results": _with_urls(rows),
                "more_url": url_for("main.search", q=q, ds=ds.key),
            })
    return grand_total, groups


@main.route("/api/search")
def api_search():
    """Live search for the modal. Returns JSON; empty until MIN_SEARCH_LEN chars."""
    q = (request.args.get("q") or "").strip()
    field = request.args.get("field", "")
    dataset = datasets_mod.get(request.args.get("ds"))
    cross = request.args.get("scope") == "all"

    if len(q) < MIN_SEARCH_LEN:
        return jsonify({"total": 0, "groups": []})

    if cross:
        total, groups = _search_all(q, CROSS_GROUP_LIMIT)
    else:
        total, rows = _search_top(dataset, q, field, LIVE_SEARCH_LIMIT)
        groups = [{
            "dataset": dataset.key,
            "title": dataset.title,
            "theme": dataset.theme,
            "total": total,
            "results": _with_urls(rows),
            "more_url": url_for("main.search", q=q, ds=dataset.key, field=field),
        }] if total else []
    return jsonify({"total": total, "groups": groups})


@main.route("/search")
def search():
    q = (request.args.get("q") or "").strip()
    field = request.args.get("field", "")
    dataset = datasets_mod.get(request.args.get("ds"))
    cross = request.args.get("scope") == "all"
    try:
        page = max(1, int(request.args.get("page", 1)))
    except (TypeError, ValueError):
        page = 1

    if cross:
        total, groups = (_search_all(q, CROSS_GROUP_LIMIT) if q else (0, []))
        return render_template(
            "search.html",
            q=q, field=field, dataset=dataset, cross=True,
            groups=groups, total=total,
            page=1, total_pages=1, page_items=[], page_size=PAGE_SIZE,
        )

    total, rows = 0, []
    if q:
        total, rows, page = _search_page(dataset, q, field, page, PAGE_SIZE)

    total_pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
    return render_template(
        "search.html",
        q=q, field=field, dataset=dataset, cross=False,
        results=_with_urls(rows), total=total,
        page=page, total_pages=total_pages, page_size=PAGE_SIZE,
        page_items=page_window(page, total_pages),
    )
