"""Find & replace — IDE-style: options, per-match review, replace all.

Works against any archive: the scope dropdown lists the active dataset's fields,
and matching runs over that dataset's own columns. Regex, case sensitivity and
whole-word are honoured as literal Python `re` semantics.
"""

from __future__ import annotations

import re

from flask import jsonify, render_template, request
from markupsafe import escape
from sqlalchemy import select

import datasets as datasets_mod
from datasets import Dataset
from extensions import db
from views import fields_for, main, scan_rows

#: Records updated per flush when replacing across a whole archive.
CHUNK = 500


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


def _pattern_from_args(source) -> tuple[re.Pattern | None, str | None]:
    """Build the pattern from a query string or a JSON body."""
    if hasattr(source, "get") and not isinstance(source, dict):
        return _build_pattern(
            source.get("q", ""),
            source.get("cc") == "1",
            source.get("w") == "1",
            source.get("re") == "1",
        )
    return _build_pattern(
        source.get("q", ""), bool(source.get("cc")),
        bool(source.get("w")), bool(source.get("re")),
    )


def _highlight_all(value, pattern):
    """Full value, HTML-escaped, with every match wrapped in <mark>."""
    if not value:
        return ""
    parts, last = [], 0
    for mm in pattern.finditer(value):
        if mm.start() == mm.end():
            continue  # skip zero-width matches
        parts.append(str(escape(value[last:mm.start()])))
        parts.append("<mark>" + str(escape(mm.group(0))) + "</mark>")
        last = mm.end()
    parts.append(str(escape(value[last:])))
    return "".join(parts)


def _replacer(repl, regex):
    """A replacement argument for re.sub; literal (no backrefs) unless regex."""
    return repl if regex else (lambda m: repl)


def _apply_sub(record, pattern, repl_arg, fields) -> bool:
    changed = False
    for f in fields:
        val = getattr(record, f) or ""
        new = pattern.sub(repl_arg, val)
        if new != val:
            setattr(record, f, new)
            changed = True
    return changed


def _matching_ids(dataset: Dataset, pattern, fields, exclude=()) -> list[int]:
    excluded = set(exclude)
    return [
        row["id"] for row in scan_rows(dataset)
        if row["id"] not in excluded
        and any(pattern.search(row[f] or "") for f in fields)
    ]


@main.route("/replace")
def replace():
    return render_template("replace.html", dataset=datasets_mod.get(request.args.get("ds")))


@main.route("/api/replace/find")
def api_replace_find():
    """All matching record ids (no limit). Previews are fetched per match on demand."""
    pattern, err = _pattern_from_args(request.args)
    if err:
        return jsonify({"error": err}), 400
    if pattern is None:
        return jsonify({"total": 0, "ids": []})
    dataset = datasets_mod.get(request.args.get("ds"))
    fields = fields_for(dataset, request.args.get("field", ""))
    ids = _matching_ids(dataset, pattern, fields)
    return jsonify({"total": len(ids), "ids": ids})


@main.route("/api/replace/preview")
def api_replace_preview():
    """Highlighted preview of one record for the given search."""
    pattern, err = _pattern_from_args(request.args)
    if err or pattern is None:
        return jsonify({"error": err or "empty"}), 400
    dataset = datasets_mod.get(request.args.get("ds"))
    record = db.session.get(dataset.model, request.args.get("id", type=int))
    if record is None:
        return jsonify({"error": "not found"}), 404
    scope = set(fields_for(dataset, request.args.get("field", "")))
    hl = {
        f: (_highlight_all(getattr(record, f) or "", pattern) if f in scope
            else str(escape(getattr(record, f) or "")))
        for f in dataset.searchable_fields
    }
    html = render_template(
        "_replace_preview.html", record=record, hl=hl, dataset=dataset,
    )
    return jsonify({"id": record.id, "html": html})


@main.route("/api/replace/one", methods=["POST"])
def api_replace_one():
    data = request.get_json(silent=True) or {}
    pattern, err = _pattern_from_args(data)
    if err or pattern is None:
        return jsonify({"ok": False, "error": err or "empty"}), 400
    dataset = datasets_mod.get(data.get("ds"))
    record = db.session.get(dataset.model, data.get("id"))
    if record is None:
        return jsonify({"ok": False, "error": "not found"}), 404
    fields = fields_for(dataset, data.get("field", ""))
    changed = _apply_sub(
        record, pattern, _replacer(data.get("repl", ""), data.get("re")), fields)
    if changed:
        db.session.commit()
    return jsonify({"ok": True, "changed": changed})


@main.route("/api/replace/all", methods=["POST"])
def api_replace_all():
    data = request.get_json(silent=True) or {}
    pattern, err = _pattern_from_args(data)
    if err or pattern is None:
        return jsonify({"ok": False, "error": err or "empty"}), 400
    dataset = datasets_mod.get(data.get("ds"))
    fields = fields_for(dataset, data.get("field", ""))
    ids = _matching_ids(dataset, pattern, fields, data.get("exclude", []))
    repl_arg = _replacer(data.get("repl", ""), data.get("re"))
    model = dataset.model

    count = 0
    for start in range(0, len(ids), CHUNK):
        chunk = ids[start:start + CHUNK]
        for record in db.session.scalars(select(model).where(model.id.in_(chunk))):
            if _apply_sub(record, pattern, repl_arg, fields):
                count += 1
    if count:
        db.session.commit()
    return jsonify({"ok": True, "count": count})
