"""The application blueprint and the helpers its route modules share.

Routes are dataset-scoped: every archive is served by the same view functions,
which read the active :class:`datasets.Dataset` from the URL. The dataset is
also stashed on ``flask.g`` so templates (sidebar, navbar, search modal) can see
it without every route passing it explicitly.

URL shape:

    /                     Τραγούδια dashboard (legacy entry point)
    /<ds>/                dashboard
    /<ds>/new             add a record
    /<ds>/<id>            view, edit and save a record
    /<ds>/<id>/delete     delete a record
    /<ds>/goto            pager jump-to-id
    /search, /replace     dataset taken from the ?ds= parameter
    /admin/...            dataset-agnostic

where ``<ds>`` is one of the registry slugs: songs, 45, 78, bios. The slug is
matched by a custom converter, so these rules can never shadow /search or
/admin.
"""

from __future__ import annotations

import re

from flask import Blueprint, g, request
from werkzeug.routing import BaseConverter

import datasets as datasets_mod
from datasets import DATASET_KEYS, DATASET_LIST, DATASETS, Dataset
from extensions import db

main = Blueprint("main", __name__)

PAGE_SIZE = 25
LIVE_SEARCH_LIMIT = 20
MIN_SEARCH_LEN = 3


class DatasetConverter(BaseConverter):
    """URL converter matching only registered dataset slugs.

    Yields the :class:`~datasets.Dataset` itself, so views receive the
    descriptor rather than a string, and accepts either form when building URLs.
    """

    def __init__(self, url_map):
        super().__init__(url_map)
        self.regex = "|".join(re.escape(k) for k in DATASET_KEYS)

    def to_python(self, value: str) -> Dataset:
        return DATASETS[value]

    def to_url(self, value) -> str:
        return value.key if isinstance(value, Dataset) else str(value)


# --------------------------------------------------------------------------- #
# Active dataset
# --------------------------------------------------------------------------- #

@main.url_value_preprocessor
def _remember_dataset(endpoint, values):
    """Pick up the dataset from the URL before the request runs."""
    if values and isinstance(values.get("dataset"), Dataset):
        g.dataset = values["dataset"]


@main.before_request
def _default_dataset():
    """Dataset-agnostic routes (/search, /replace) read it from ?ds=."""
    if not hasattr(g, "dataset"):
        g.dataset = datasets_mod.get(request.args.get("ds"))


@main.app_context_processor
def _inject_dataset():
    """Everything the chrome (sidebar, navbar, search modal) needs."""
    dataset = getattr(g, "dataset", datasets_mod.DEFAULT_DATASET)
    return {
        "dataset": dataset,
        "all_datasets": DATASET_LIST,
        "field_options": dataset.field_options,
    }


# --------------------------------------------------------------------------- #
# Formatting
# --------------------------------------------------------------------------- #

@main.app_template_filter("gr")
def group_number(value):
    """Format an integer with '.' thousands separators (Greek convention)."""
    try:
        return f"{int(value):,}".replace(",", ".")
    except (TypeError, ValueError):
        return value


# --------------------------------------------------------------------------- #
# Shared query helpers
# --------------------------------------------------------------------------- #

def fts_query(query: str) -> str:
    """Build a safe FTS5 prefix query from free user input.

    Folds accents, keeps only word characters, and turns each token into a
    prefix term so partial words match. Returns "" when there's nothing to run.
    """
    from models import fold

    tokens = re.findall(r"\w+", fold(query), flags=re.UNICODE)
    return " ".join(f"{t}*" for t in tokens)


def neighbours(dataset: Dataset, rec_id: int) -> tuple[int | None, int | None]:
    """Previous/next record ids by position (gap-tolerant).

    The disc archives are numbered with gaps, so this walks to the nearest
    existing id rather than assuming id ± 1.
    """
    from sqlalchemy import func, select

    model = dataset.model
    prev_id = db.session.scalar(select(func.max(model.id)).where(model.id < rec_id))
    next_id = db.session.scalar(select(func.min(model.id)).where(model.id > rec_id))
    return prev_id, next_id


def page_window(page: int, total_pages: int, edge: int = 1, around: int = 2) -> list:
    """Page numbers to show, with None marking an ellipsis gap."""
    if total_pages <= 1:
        return []
    wanted = set(range(1, edge + 1))
    wanted |= set(range(total_pages - edge + 1, total_pages + 1))
    wanted |= set(range(page - around, page + around + 1))
    ordered = sorted(p for p in wanted if 1 <= p <= total_pages)
    out: list = []
    prev = 0
    for p in ordered:
        if p - prev > 1:
            out.append(None)
        out.append(p)
        prev = p
    return out


def scan_rows(dataset: Dataset):
    """Lightweight scan of id + all text fields (Core rows, keyed by column)."""
    from sqlalchemy import select

    model = dataset.model
    cols = [model.id] + [getattr(model, f) for f in dataset.searchable_fields]
    return db.session.execute(select(*cols).order_by(model.id)).mappings()


def fields_for(dataset: Dataset, field: str | None):
    """The field(s) a search/replace targets: one field, or all if unspecified."""
    fields = dataset.searchable_fields
    return (field,) if field in fields else fields


def field_hits(dataset: Dataset, q: str, field: str):
    """Records whose `field` contains every folded token of q (accent-insensitive)."""
    from models import fold

    tokens = fold(q).split()
    if not tokens:
        return []
    return [row for row in scan_rows(dataset)
            if all(t in fold(row[field] or "") for t in tokens)]


def result_row(dataset: Dataset, row) -> dict:
    """Normalize a search hit into what the result templates render."""
    getter = row.get if hasattr(row, "get") else lambda k: getattr(row, k, "")
    return {
        "id": getter("id"),
        "title": getter(dataset.result_title) or "",
        "meta": [getter(a) or "" for a in dataset.result_meta],
        "dataset": dataset.key,
        "dataset_title": dataset.title,
    }


# Route modules register themselves on `main` at import time. Imported last so
# they can import the helpers above without a circular import.
from views import admin, records, replace, search  # noqa: E402,F401
