"""Admin area (password-protected via the ADMIN_PASSWORD env var).

Import accepts either a per-archive CSV or a full .db restore. A CSV replaces
only the archive it was uploaded for; a .db restores every archive it contains
and reports any it doesn't.
"""

from __future__ import annotations

import datetime
import hmac
import os
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
    read_rows,
    replace_all,
    replace_from_db,
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
    """Import an uploaded CSV or .db file (auto-detected). Returns (ok, message)."""
    if not upload or not upload.filename:
        return False, "Δεν επιλέχθηκε αρχείο."
    fd, tmp = tempfile.mkstemp(prefix="disco-upload-")
    os.close(fd)
    try:
        upload.save(tmp)
        with open(tmp, "rb") as fh:
            is_sqlite = fh.read(16) == _SQLITE_MAGIC
        return _import_db(tmp) if is_sqlite else _import_csv_file(tmp, dataset)
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


def _import_db(path: str) -> tuple[bool, str]:
    ok, msg = validate_sqlite_db(path)
    if not ok:
        return False, msg
    db.session.remove()  # release the ORM connection before the raw file write
    try:
        counts, skipped = replace_from_db(path, current_app.config["DB_PATH"])
    except Exception:
        return False, "Η αποκατάσταση απέτυχε· η βάση δεν άλλαξε."

    by_table = {d.model.__tablename__: d.title for d in DATASET_LIST}
    restored = ", ".join(
        f"{by_table.get(t, t)}: {group_number(n)}" for t, n in counts.items()
    )
    message = f"Η βάση αποκαταστάθηκε από αρχείο .db — {restored}."
    if skipped:
        names = ", ".join(by_table.get(t, t) for t in skipped)
        message += (f" Δεν βρέθηκαν στο αρχείο και παρέμειναν ως ήταν: {names}.")
    return True, message


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
