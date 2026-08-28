"""Admin area (password-protected via the ADMIN_PASSWORD env var).

Import accepts either a per-archive CSV or a full .db restore. A CSV replaces
only the archive it was uploaded for; a .db restores every archive it contains
and reports any it doesn't.
"""

from __future__ import annotations

import datetime
import hmac
import os
import secrets
import tempfile
from functools import wraps

from flask import (
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

import datasets as datasets_mod
from dataio import (
    CSVFormatError,
    backup_database,
    inspect_db,
    list_backups,
    live_counts,
    read_rows,
    replace_all,
    replace_from_db,
    schema_version,
    validate_sqlite_db,
)
from datasets import DATASET_LIST, Dataset
from extensions import db
from views import group_number, main

_SQLITE_MAGIC = b"SQLite format 3\x00"


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


def _do_import(upload, dataset: Dataset) -> tuple[bool, str]:
    """Import an uploaded CSV into one archive. Returns (ok, message).

    A .db upload is refused here rather than silently taking a different code
    path: restoring a database and replacing one archive's rows are different
    operations with different blast radii, so each has its own screen.
    """
    if not upload or not upload.filename:
        return False, "Δεν επιλέχθηκε αρχείο."
    fd, tmp = tempfile.mkstemp(prefix="disco-upload-")
    os.close(fd)
    try:
        upload.save(tmp)
        with open(tmp, "rb") as fh:
            if fh.read(16) == _SQLITE_MAGIC:
                return False, ("Αυτό είναι αρχείο βάσης .db. Χρησιμοποιήστε την "
                               "«Επαναφορά βάσης» για να το αποκαταστήσετε.")
        return _import_csv_file(tmp, dataset)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def _import_csv_file(path: str, dataset: Dataset) -> tuple[bool, str]:
    try:
        with open(path, encoding="utf-8-sig", newline="") as fh:
            rows = read_rows(dataset, fh)
    except CSVFormatError as exc:
        return False, f"Μη έγκυρο αρχείο: {exc}"
    except UnicodeDecodeError:
        return False, "Το αρχείο δεν είναι έγκυρο CSV (UTF-8)."
    if not rows:
        return False, "Το αρχείο δεν περιέχει εγγραφές."
    try:
        total = replace_all(dataset, rows)
    except Exception:
        return False, "Η εισαγωγή απέτυχε· η βάση δεν άλλαξε."
    return True, (f"Το αρχείο «{dataset.title}» αντικαταστάθηκε με "
                  f"{group_number(total)} εγγραφές (CSV).")


@main.route("/admin/import", methods=["GET", "POST"])
@admin_required
def admin_import():
    dataset = datasets_mod.get(request.values.get("ds"))
    if request.method == "POST":
        ok, message = _do_import(request.files.get("csv"), dataset)
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"ok": ok, "message": message}), (200 if ok else 400)
        flash(message, "success" if ok else "danger")
        return redirect(url_for("main.admin_import", ds=dataset.key))
    return render_template(
        "admin/import.html",
        active="import",
        dataset=dataset,
        columns=list(dataset.model.CSV_COLUMNS),
        id_column=dataset.model.CSV_ID_COLUMN,
    )


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


# --------------------------------------------------------------------------- #
# Restore the whole database from an uploaded .db
#
# Two steps on purpose. The upload is staged on the volume and inspected first,
# so the admin confirms against a real comparison of row counts rather than a
# filename. Only then is the live database snapshotted and the rows copied in.
# --------------------------------------------------------------------------- #

#: Session key holding the staged upload's filename between the two steps.
_STAGED = "restore_staged"

#: Prefix for staged uploads, so an abandoned one is recognisable on the volume.
_STAGE_PREFIX = "restore-staging-"


def _db_dir() -> str:
    return os.path.dirname(os.path.abspath(current_app.config["DB_PATH"])) or "."


def _staged_path() -> str | None:
    """The staged upload for this session, if it still exists on disk."""
    name = session.get(_STAGED)
    if not name:
        return None
    # Only ever trust the basename from the session, never a path.
    path = os.path.join(_db_dir(), os.path.basename(name))
    return path if os.path.exists(path) else None


def _discard_staged() -> None:
    path = _staged_path()
    if path:
        try:
            os.remove(path)
        except OSError:
            pass
    session.pop(_STAGED, None)


def _archive_titles() -> dict[str, str]:
    return {d.model.__tablename__: d.title for d in DATASET_LIST}


@main.route("/admin/restore")
@admin_required
def admin_restore():
    _discard_staged()          # a fresh visit abandons any half-finished upload
    return render_template(
        "admin/restore.html",
        active="restore",
        current=live_counts(),
        titles=_archive_titles(),
        schema=schema_version(current_app.config["DB_PATH"]),
        backups=list_backups(current_app.config["DB_PATH"]),
        keep_backups=current_app.config.get("RESTORE_BACKUPS", 3),
    )


@main.route("/admin/restore/inspect", methods=["POST"])
@admin_required
def admin_restore_inspect():
    """Stage an uploaded .db and report what restoring it would change."""
    upload = request.files.get("db")
    if not upload or not upload.filename:
        return jsonify({"ok": False, "message": "Δεν επιλέχθηκε αρχείο."}), 400

    _discard_staged()
    staged = os.path.join(_db_dir(), f"{_STAGE_PREFIX}{secrets.token_hex(8)}.db")
    try:
        upload.save(staged)
    except OSError as exc:
        return jsonify({"ok": False,
                        "message": f"Η μεταφόρτωση απέτυχε: {exc}"}), 400

    with open(staged, "rb") as fh:
        if fh.read(16) != _SQLITE_MAGIC:
            os.remove(staged)
            return jsonify({"ok": False, "message": (
                "Αυτό δεν είναι αρχείο βάσης .db. Για CSV χρησιμοποιήστε την "
                "«Εισαγωγή CSV».")}), 400

    ok, message = validate_sqlite_db(staged)
    if not ok:
        os.remove(staged)
        return jsonify({"ok": False, "message": message}), 400

    info = inspect_db(staged)
    current = live_counts()
    titles = _archive_titles()
    live_schema = schema_version(current_app.config["DB_PATH"])

    rows = []
    for table, title in titles.items():
        incoming = info["counts"].get(table)
        rows.append({
            "table": table,
            "title": title,
            "current": current.get(table, 0),
            "incoming": incoming,          # None => absent from the upload
            "kept": incoming is None,
        })

    session[_STAGED] = os.path.basename(staged)
    return jsonify({
        "ok": True,
        "filename": upload.filename,
        "size_mb": round(os.path.getsize(staged) / (1024 * 1024), 1),
        "archives": rows,
        "schema": info["schema_version"],
        "live_schema": live_schema,
        "schema_matches": info["schema_version"] == live_schema,
    })


@main.route("/admin/restore/apply", methods=["POST"])
@admin_required
def admin_restore_apply():
    """Snapshot the live database, then copy the staged upload's rows in."""
    staged = _staged_path()
    if not staged:
        return jsonify({"ok": False, "message": (
            "Το αρχείο δεν βρέθηκε. Ανεβάστε το ξανά.")}), 400

    db_path = current_app.config["DB_PATH"]
    keep = current_app.config.get("RESTORE_BACKUPS", 3)
    db.session.remove()        # release the ORM connection before raw writes

    snapshot = None
    if keep:
        try:
            snapshot = backup_database(db_path, keep=keep)
        except OSError as exc:
            # Out of disk: refuse rather than overwrite with no way back.
            return jsonify({"ok": False, "message": str(exc)}), 400
        except Exception:
            return jsonify({"ok": False, "message": (
                "Το αντίγραφο ασφαλείας απέτυχε· η βάση δεν άλλαξε.")}), 500

    try:
        counts, skipped = replace_from_db(staged, db_path)
    except Exception:
        detail = (f" Το αντίγραφο ασφαλείας «{os.path.basename(snapshot)}» "
                  "παραμένει διαθέσιμο." if snapshot else "")
        return jsonify({"ok": False, "message": (
            "Η επαναφορά απέτυχε· η βάση δεν άλλαξε." + detail)}), 500
    finally:
        _discard_staged()

    titles = _archive_titles()
    restored = " · ".join(
        f"{titles.get(t, t)}: {group_number(n)}" for t, n in counts.items())
    message = f"Η βάση αποκαταστάθηκε — {restored}."
    if skipped:
        names = ", ".join(titles.get(t, t) for t in skipped)
        message += f" Παρέμειναν ως ήταν (απουσίαζαν από το αρχείο): {names}."
    if snapshot:
        message += f" Αντίγραφο ασφαλείας: «{os.path.basename(snapshot)}»."
    return jsonify({"ok": True, "message": message, "reload": True})


@main.route("/admin/restore/cancel", methods=["POST"])
@admin_required
def admin_restore_cancel():
    _discard_staged()
    return jsonify({"ok": True})
