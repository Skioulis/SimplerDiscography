"""HTTP routes for SimpleDiscography."""

from __future__ import annotations

import datetime
import hmac
import os
import re
import tempfile
from functools import wraps

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from markupsafe import escape
from sqlalchemy import func, select, text

from dataio import (
    CSVFormatError,
    read_song_rows,
    replace_all_songs,
    replace_songs_from_db,
    validate_sqlite_db,
)
from extensions import db
from models import Song, fold
from stats import dashboard_stats

main = Blueprint("main", __name__)

PAGE_SIZE = 25
LIVE_SEARCH_LIMIT = 20
MIN_SEARCH_LEN = 3


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


def _page_window(page: int, total_pages: int, edge: int = 1, around: int = 2) -> list:
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


def _song_rows():
    """Lightweight scan of id + all text fields (Core rows, keyed by column)."""
    cols = [Song.id] + [getattr(Song, f) for f in Song.SEARCHABLE_FIELDS]
    return db.session.execute(select(*cols).order_by(Song.id)).mappings()


def _fields_for(field):
    """The field(s) a search/replace targets: one field, or all if unspecified."""
    return (field,) if field in Song.SEARCHABLE_FIELDS else Song.SEARCHABLE_FIELDS


def _field_hits(q, field):
    """Songs whose `field` contains every folded token of q (accent-insensitive)."""
    tokens = fold(q).split()
    if not tokens:
        return []
    return [row for row in _song_rows()
            if all(t in fold(row[field] or "") for t in tokens)]


@main.app_context_processor
def _inject_field_options():
    """Field choices for the search/replace scope dropdowns (used in templates)."""
    return {"field_options": [(f, Song.LABELS[f]) for f in Song.SEARCHABLE_FIELDS]}


@main.route("/")
def dashboard():
    return render_template("dashboard.html", stats=dashboard_stats())


@main.route("/songs/new")
def song_new():
    """Blank form for adding a song (same layout as the record view)."""
    return render_template("new.html", song=Song())


@main.route("/songs/new", methods=["POST"])
def song_create():
    song = Song()
    for attr in Song.SEARCHABLE_FIELDS:
        setattr(song, attr, (request.form.get(attr) or "").strip())
    db.session.add(song)
    db.session.commit()  # sets search_blob + timestamps, FTS trigger indexes it
    flash("Το τραγούδι προστέθηκε.")
    return redirect(url_for("main.record", song_id=song.id))


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


@main.route("/songs/<int:song_id>/delete", methods=["POST"])
def song_delete(song_id: int):
    song = db.session.get(Song, song_id)
    if song is None:
        abort(404)
    prev_id, next_id = _neighbours(song_id)
    db.session.delete(song)
    db.session.commit()  # AFTER DELETE trigger removes it from the FTS index
    flash("Το τραγούδι διαγράφηκε.")
    target = next_id or prev_id
    if target:
        return redirect(url_for("main.record", song_id=target))
    return redirect(url_for("main.dashboard"))


def _row_dict(r):
    return {"id": r["id"], "title": r["title"], "composer": r["composer"], "lyricist": r["lyricist"]}


@main.route("/api/search")
def api_search():
    """Live search for the modal. Returns JSON; empty until MIN_SEARCH_LEN chars."""
    q = (request.args.get("q") or "").strip()
    field = request.args.get("field", "")
    results: list = []
    total = 0
    if len(q) >= MIN_SEARCH_LEN:
        if field in Song.SEARCHABLE_FIELDS:
            hits = _field_hits(q, field)
            total = len(hits)
            results = [_row_dict(r) for r in hits[:LIVE_SEARCH_LIMIT]]
        elif (fts := _fts_query(q)):
            rows = db.session.execute(
                text(
                    "SELECT s.id, s.title, s.composer, s.lyricist "
                    "FROM song_fts f JOIN song s ON s.id = f.rowid "
                    "WHERE song_fts MATCH :q ORDER BY rank LIMIT :lim"
                ),
                {"q": fts, "lim": LIVE_SEARCH_LIMIT},
            ).mappings().all()
            results = [_row_dict(r) for r in rows]
            total = db.session.execute(
                text("SELECT COUNT(*) FROM song_fts WHERE song_fts MATCH :q"),
                {"q": fts},
            ).scalar_one()
    return jsonify({"results": results, "total": total})


@main.route("/search")
def search():
    q = (request.args.get("q") or "").strip()
    field = request.args.get("field", "")
    try:
        page = max(1, int(request.args.get("page", 1)))
    except (TypeError, ValueError):
        page = 1

    results: list = []
    total = 0
    if q and field in Song.SEARCHABLE_FIELDS:
        hits = _field_hits(q, field)
        total = len(hits)
        page = min(page, (total + PAGE_SIZE - 1) // PAGE_SIZE) if total else page
        results = [_row_dict(r) for r in hits[(page - 1) * PAGE_SIZE: page * PAGE_SIZE]]
    elif q and (fts := _fts_query(q)):
        total = db.session.execute(
            text("SELECT COUNT(*) FROM song_fts WHERE song_fts MATCH :q"),
            {"q": fts},
        ).scalar_one()
        page = min(page, (total + PAGE_SIZE - 1) // PAGE_SIZE) if total else page
        results = db.session.execute(
            text(
                "SELECT s.id, s.title, s.composer, s.lyricist "
                "FROM song_fts f JOIN song s ON s.id = f.rowid "
                "WHERE song_fts MATCH :q ORDER BY rank LIMIT :lim OFFSET :off"
            ),
            {"q": fts, "lim": PAGE_SIZE, "off": (page - 1) * PAGE_SIZE},
        ).mappings().all()

    total_pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
    return render_template(
        "search.html",
        q=q,
        field=field,
        results=results,
        total=total,
        page=page,
        total_pages=total_pages,
        page_size=PAGE_SIZE,
        page_items=_page_window(page, total_pages),
    )


# --------------------------------------------------------------------------- #
# Find & replace (main area) — IDE-style: options, per-match review, replace all
# --------------------------------------------------------------------------- #

def _build_pattern(find, case_sensitive, whole_word, regex):
    """Compile a search pattern from the options. Returns (pattern, error)."""
    if not find:
        return None, None
    flags = 0 if case_sensitive else re.IGNORECASE
    try:
        if regex:
            return re.compile(find, flags), None
        body = re.escape(find)
        if whole_word:
            body = r"\b" + body + r"\b"
        return re.compile(body, flags), None
    except re.error as exc:
        return None, str(exc)


def _highlight(value, pattern, ctx=50):
    """Escaped snippet around the first match, with all matches wrapped in <mark>."""
    m = pattern.search(value)
    if not m:
        return None
    start, end = max(0, m.start() - ctx), min(len(value), m.end() + ctx)
    seg = value[start:end]
    parts, last = [], 0
    for mm in pattern.finditer(seg):
        if mm.start() == mm.end():
            continue  # skip zero-width matches
        parts.append(str(escape(seg[last:mm.start()])))
        parts.append("<mark>" + str(escape(mm.group(0))) + "</mark>")
        last = mm.end()
    parts.append(str(escape(seg[last:])))
    return ("…" if start else "") + "".join(parts) + ("…" if end < len(value) else "")


def _highlight_all(value, pattern):
    """Full value, HTML-escaped, with every match wrapped in <mark> (no truncation)."""
    if not value:
        return ""
    parts, last = [], 0
    for mm in pattern.finditer(value):
        if mm.start() == mm.end():
            continue
        parts.append(str(escape(value[last:mm.start()])))
        parts.append("<mark>" + str(escape(mm.group(0))) + "</mark>")
        last = mm.end()
    parts.append(str(escape(value[last:])))
    return "".join(parts)


def _replacer(repl, regex):
    """A str->str replacement argument for re.sub; literal (no backrefs) unless regex."""
    return repl if regex else (lambda m: repl)


def _apply_sub(song, pattern, repl_arg, fields):
    changed = False
    for f in fields:
        val = getattr(song, f) or ""
        new = pattern.sub(repl_arg, val)
        if new != val:
            setattr(song, f, new)
            changed = True
    return changed


@main.route("/replace")
def replace():
    return render_template("replace.html")


@main.route("/api/replace/find")
def api_replace_find():
    """All matching record ids (no limit). Previews are fetched per match on demand."""
    pattern, err = _build_pattern(
        request.args.get("q", ""),
        request.args.get("cc") == "1",
        request.args.get("w") == "1",
        request.args.get("re") == "1",
    )
    if err:
        return jsonify({"error": err}), 400
    if pattern is None:
        return jsonify({"total": 0, "ids": []})
    fields = _fields_for(request.args.get("field", ""))
    ids = [
        row["id"] for row in _song_rows()
        if any(pattern.search(row[f] or "") for f in fields)
    ]
    return jsonify({"total": len(ids), "ids": ids})


@main.route("/api/replace/preview")
def api_replace_preview():
    """Highlighted preview of one record for the given search (used while navigating)."""
    pattern, err = _build_pattern(
        request.args.get("q", ""),
        request.args.get("cc") == "1",
        request.args.get("w") == "1",
        request.args.get("re") == "1",
    )
    if err or pattern is None:
        return jsonify({"error": err or "empty"}), 400
    song = db.session.get(Song, request.args.get("id", type=int))
    if song is None:
        return jsonify({"error": "not found"}), 404
    scope = set(_fields_for(request.args.get("field", "")))
    hl = {
        f: (_highlight_all(getattr(song, f) or "", pattern) if f in scope
            else str(escape(getattr(song, f) or "")))
        for f in Song.SEARCHABLE_FIELDS
    }
    html = render_template("_replace_preview.html", song=song, hl=hl, labels=Song.LABELS)
    return jsonify({"id": song.id, "html": html})


@main.route("/api/replace/one", methods=["POST"])
def api_replace_one():
    data = request.get_json(silent=True) or {}
    pattern, err = _build_pattern(
        data.get("q", ""), bool(data.get("cc")), bool(data.get("w")), bool(data.get("re")))
    if err or pattern is None:
        return jsonify({"ok": False, "error": err or "empty"}), 400
    song = db.session.get(Song, data.get("id"))
    if song is None:
        return jsonify({"ok": False, "error": "not found"}), 404
    fields = _fields_for(data.get("field", ""))
    changed = _apply_sub(song, pattern, _replacer(data.get("repl", ""), data.get("re")), fields)
    if changed:
        db.session.commit()
    return jsonify({"ok": True, "changed": changed})


@main.route("/api/replace/all", methods=["POST"])
def api_replace_all():
    data = request.get_json(silent=True) or {}
    pattern, err = _build_pattern(
        data.get("q", ""), bool(data.get("cc")), bool(data.get("w")), bool(data.get("re")))
    if err or pattern is None:
        return jsonify({"ok": False, "error": err or "empty"}), 400
    exclude = set(data.get("exclude", []))
    fields = _fields_for(data.get("field", ""))
    ids = [
        row["id"] for row in _song_rows()
        if row["id"] not in exclude
        and any(pattern.search(row[f] or "") for f in fields)
    ]
    repl_arg = _replacer(data.get("repl", ""), data.get("re"))
    count = 0
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        for song in db.session.scalars(select(Song).where(Song.id.in_(chunk))):
            if _apply_sub(song, pattern, repl_arg, fields):
                count += 1
    if count:
        db.session.commit()
    return jsonify({"ok": True, "count": count})


# --------------------------------------------------------------------------- #
# Admin (password-protected via the ADMIN_PASSWORD env var)
# --------------------------------------------------------------------------- #

def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_app.config.get("ADMIN_PASSWORD"):
            abort(503)  # admin not configured
        if not session.get("is_admin"):
            return redirect(url_for("main.admin_login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


@main.route("/admin")
@admin_required
def admin_home():
    return redirect(url_for("main.admin_import"))


@main.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if not current_app.config.get("ADMIN_PASSWORD"):
        abort(503)
    if session.get("is_admin"):
        return redirect(url_for("main.admin_import"))
    if request.method == "POST":
        supplied = request.form.get("password", "")
        if hmac.compare_digest(supplied, current_app.config["ADMIN_PASSWORD"]):
            session["is_admin"] = True
            nxt = request.args.get("next", "")
            return redirect(nxt if nxt.startswith("/admin") else url_for("main.admin_import"))
        flash("Λάθος κωδικός.", "danger")
    return render_template("admin/login.html")


@main.route("/admin/logout")
def admin_logout():
    session.pop("is_admin", None)
    flash("Αποσυνδεθήκατε.")
    return redirect(url_for("main.dashboard"))


_SQLITE_MAGIC = b"SQLite format 3\x00"


def _do_import(upload) -> tuple[bool, str]:
    """Import an uploaded CSV or .db file (auto-detected). Returns (ok, message)."""
    if not upload or not upload.filename:
        return False, "Δεν επιλέχθηκε αρχείο."
    fd, tmp = tempfile.mkstemp(prefix="disco-upload-")
    os.close(fd)
    try:
        upload.save(tmp)
        with open(tmp, "rb") as fh:
            is_sqlite = fh.read(16) == _SQLITE_MAGIC
        return _import_db(tmp) if is_sqlite else _import_csv_file(tmp)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def _import_csv_file(path: str) -> tuple[bool, str]:
    try:
        with open(path, encoding="utf-8-sig", newline="") as fh:
            rows = read_song_rows(fh)
    except CSVFormatError as exc:
        return False, f"Μη έγκυρο αρχείο: {exc}"
    except UnicodeDecodeError:
        return False, "Το αρχείο δεν είναι έγκυρο CSV (UTF-8)."
    if not rows:
        return False, "Το αρχείο δεν περιέχει εγγραφές."
    try:
        total = replace_all_songs(rows)
    except Exception:
        return False, "Η εισαγωγή απέτυχε· η βάση δεν άλλαξε."
    return True, f"Η βάση αντικαταστάθηκε με {group_number(total)} τραγούδια (CSV)."


def _import_db(path: str) -> tuple[bool, str]:
    ok, msg = validate_sqlite_db(path)
    if not ok:
        return False, msg
    db.session.remove()  # release the ORM connection before the raw file write
    try:
        total = replace_songs_from_db(path, current_app.config["DB_PATH"])
    except Exception:
        return False, "Η αποκατάσταση απέτυχε· η βάση δεν άλλαξε."
    return True, f"Η βάση αποκαταστάθηκε από αρχείο .db με {group_number(total)} τραγούδια."


@main.route("/admin/import", methods=["GET", "POST"])
@admin_required
def admin_import():
    if request.method == "POST":
        ok, message = _do_import(request.files.get("csv"))
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"ok": ok, "message": message}), (200 if ok else 400)
        flash(message, "success" if ok else "danger")
        return redirect(url_for("main.admin_import"))
    return render_template("admin/import.html", active="import", columns=list(Song.CSV_COLUMNS))


@main.route("/admin/download")
@admin_required
def admin_download():
    path = current_app.config["DB_PATH"]
    size = os.path.getsize(path) if os.path.exists(path) else 0
    return render_template(
        "admin/download.html", active="download", db_size=f"{size / (1024 * 1024):.1f} MB"
    )


@main.route("/admin/download/db")
@admin_required
def admin_download_db():
    path = current_app.config["DB_PATH"]
    if not os.path.exists(path):
        abort(404)
    name = f"discography-{datetime.date.today().isoformat()}.db"
    return send_file(path, as_attachment=True, download_name=name,
                     mimetype="application/octet-stream")
