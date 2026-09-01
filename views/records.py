"""Dashboard, record browse/edit, add and delete — for every archive."""

from __future__ import annotations

from flask import abort, flash, redirect, render_template, request, url_for
from sqlalchemy import func, select

import datasets as datasets_mod
from dataio import next_id as next_free_id
from datasets import Dataset
from extensions import db
from stats import dashboard_stats
from views import bounds, main, neighbours

#: Dashboard template per stats kind.
_DASHBOARD_TEMPLATES = {
    "songs": "dashboard.html",
    "disc": "dashboard_disc.html",
    "bios": "dashboard_bios.html",
}


def _get_or_404(dataset: Dataset, rec_id: int):
    record = db.session.get(dataset.model, rec_id)
    if record is None:
        abort(404)
    return record


def _apply_form(dataset: Dataset, record) -> None:
    """Copy submitted values onto a record, trimming whitespace."""
    for attr in dataset.searchable_fields:
        setattr(record, attr, (request.form.get(attr) or "").strip())


@main.route("/")
def dashboard():
    """Legacy entry point: the Τραγούδια dashboard."""
    return dataset_dashboard(datasets_mod.DEFAULT_DATASET)


@main.route("/<ds:dataset>/")
def dataset_dashboard(dataset: Dataset):
    return render_template(
        _DASHBOARD_TEMPLATES[dataset.stats_kind],
        stats=dashboard_stats(dataset),
        dataset=dataset,
    )


@main.route("/<ds:dataset>/new")
def record_new(dataset: Dataset):
    """Blank form for adding a record (same layout as the record view)."""
    return render_template("new.html", record=dataset.model(), dataset=dataset)


@main.route("/<ds:dataset>/new", methods=["POST"])
def record_create(dataset: Dataset):
    record = dataset.model()
    _apply_form(dataset, record)
    if dataset.model.CSV_ID_COLUMN:
        # Archives keyed by their source Αναγνωριστικό aren't autoincrement, so
        # a new record continues the numbering itself.
        record.id = next_free_id(dataset)
    db.session.add(record)
    db.session.commit()  # sets search_blob + timestamps, FTS trigger indexes it
    flash("Η εγγραφή προστέθηκε.")
    return redirect(url_for("main.record", dataset=dataset, rec_id=record.id))


@main.route("/<ds:dataset>/<int:rec_id>")
def record(dataset: Dataset, rec_id: int):
    record = _get_or_404(dataset, rec_id)
    prev_id, next_id = neighbours(dataset, rec_id)
    first_id, last_id = bounds(dataset)
    model = dataset.model
    total = db.session.scalar(select(func.count()).select_from(model))
    position = db.session.scalar(select(func.count()).where(model.id <= rec_id))
    return render_template(
        "record.html",
        record=record,
        dataset=dataset,
        prev_id=prev_id,
        next_id=next_id,
        first_id=first_id,
        last_id=last_id,
        total=total,
        position=position,
        edit=bool(request.args.get("edit")),
    )


@main.route("/<ds:dataset>/goto")
def goto(dataset: Dataset):
    """Jump to a record by id from the pager box (clamps to the nearest existing)."""
    model = dataset.model
    try:
        target = int(request.args.get("id", ""))
    except (TypeError, ValueError):
        return redirect(url_for("main.record", dataset=dataset, rec_id=1))

    min_id, max_id = bounds(dataset)
    if min_id is None:
        abort(404)

    target = max(min_id, min(target, max_id))
    if db.session.get(model, target) is None:  # land on nearest existing id
        target = (
            db.session.scalar(select(func.min(model.id)).where(model.id >= target))
            or db.session.scalar(select(func.max(model.id)).where(model.id <= target))
        )
    return redirect(url_for("main.record", dataset=dataset, rec_id=target))


@main.route("/<ds:dataset>/<int:rec_id>", methods=["POST"])
def record_save(dataset: Dataset, rec_id: int):
    record = _get_or_404(dataset, rec_id)
    _apply_form(dataset, record)
    db.session.commit()  # bumps `updated`, resyncs search_blob + FTS
    flash("Οι αλλαγές αποθηκεύτηκαν.")
    return redirect(url_for("main.record", dataset=dataset, rec_id=rec_id))


@main.route("/<ds:dataset>/<int:rec_id>/delete", methods=["POST"])
def record_delete(dataset: Dataset, rec_id: int):
    record = _get_or_404(dataset, rec_id)
    prev_id, next_id = neighbours(dataset, rec_id)
    db.session.delete(record)
    db.session.commit()  # AFTER DELETE trigger removes it from the FTS index
    flash("Η εγγραφή διαγράφηκε.")
    target = next_id or prev_id
    if target:
        return redirect(url_for("main.record", dataset=dataset, rec_id=target))
    return redirect(url_for("main.dataset_dashboard", dataset=dataset))
