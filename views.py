"""HTTP routes for SimpleDiscography."""

from __future__ import annotations

import re

from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from sqlalchemy import func, select, text

from extensions import db
from models import Song, fold
from stats import dashboard_stats

main = Blueprint("main", __name__)

SEARCH_LIMIT = 100


@main.app_template_filter("gr")
def group_number(value):
    """Format an integer with '.' thousands separators (Greek convention)."""
    try:
        return f"{int(value):,}".replace(",", ".")
    except (TypeError, ValueError):
        return value


def _fts_query(query: str) -> str:
    """Build a safe FTS5 prefix query from free user input.

    Folds accents, keeps only word characters, and turns each token into a
    prefix term so partial words match. Returns "" when there's nothing to run.
    """
    tokens = re.findall(r"\w+", fold(query), flags=re.UNICODE)
    return " ".join(f"{t}*" for t in tokens)


def _neighbours(song_id: int) -> tuple[int | None, int | None]:
    """Previous/next song ids by position (gap-tolerant)."""
    prev_id = db.session.scalar(select(func.max(Song.id)).where(Song.id < song_id))
    next_id = db.session.scalar(select(func.min(Song.id)).where(Song.id > song_id))
    return prev_id, next_id


@main.route("/")
def dashboard():
    return render_template("dashboard.html", stats=dashboard_stats())


@main.route("/songs/<int:song_id>")
def record(song_id: int):
    song = db.session.get(Song, song_id)
    if song is None:
        abort(404)
    prev_id, next_id = _neighbours(song_id)
    total = db.session.scalar(select(func.count()).select_from(Song))
    position = db.session.scalar(select(func.count()).where(Song.id <= song_id))
    return render_template(
        "record.html",
        song=song,
        prev_id=prev_id,
        next_id=next_id,
        total=total,
        position=position,
        edit=bool(request.args.get("edit")),
    )


@main.route("/goto")
def goto():
    """Jump to a record by id from the pager box (clamps to the nearest existing)."""
    try:
        target = int(request.args.get("id", ""))
    except (TypeError, ValueError):
        return redirect(url_for("main.record", song_id=1))

    min_id = db.session.scalar(select(func.min(Song.id)))
    max_id = db.session.scalar(select(func.max(Song.id)))
    if min_id is None:
        abort(404)

    target = max(min_id, min(target, max_id))
    if db.session.get(Song, target) is None:  # land on nearest existing id
        target = (
            db.session.scalar(select(func.min(Song.id)).where(Song.id >= target))
            or db.session.scalar(select(func.max(Song.id)).where(Song.id <= target))
        )
    return redirect(url_for("main.record", song_id=target))


@main.route("/songs/<int:song_id>", methods=["POST"])
def record_save(song_id: int):
    song = db.session.get(Song, song_id)
    if song is None:
        abort(404)
    for attr in Song.SEARCHABLE_FIELDS:
        setattr(song, attr, (request.form.get(attr) or "").strip())
    db.session.commit()  # bumps `updated`, resyncs search_blob + FTS
    flash("Οι αλλαγές αποθηκεύτηκαν.")
    return redirect(url_for("main.record", song_id=song_id))


@main.route("/search")
def search():
    q = (request.args.get("q") or "").strip()
    fts = _fts_query(q)
    results: list = []
    total = 0
    if fts:
        results = db.session.execute(
            text(
                "SELECT s.id, s.title, s.composer, s.lyricist "
                "FROM song_fts f JOIN song s ON s.id = f.rowid "
                "WHERE song_fts MATCH :q ORDER BY rank LIMIT :lim"
            ),
            {"q": fts, "lim": SEARCH_LIMIT},
        ).all()
        total = db.session.execute(
            text("SELECT COUNT(*) FROM song_fts WHERE song_fts MATCH :q"),
            {"q": fts},
        ).scalar_one()
    return render_template("search.html", q=q, results=results, total=total, limit=SEARCH_LIMIT)
