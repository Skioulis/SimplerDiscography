# SimpleDiscography — Δισκογραφικό Αρχείο

A web application for browsing, searching, and editing a **Greek song discography
archive** of ~56,000 recordings — composers, lyricists, lyrics, provenance, and
pressing details for rebetiko / laïkó and related repertoire, from the 78 rpm era
to today.

The interface is in Greek, matching the source archive.

---

## What it does

### 📊 Dashboard (`/`)
An at-a-glance overview of the whole collection:

- **Headline figures** — total songs, distinct composers and lyricists, and the
  time span covered.
- **Recordings by decade** — a bar chart of the collection's shape (the 1960s–70s
  peak stands out), derived from year references in the archive/notes fields.
- **Top composers & lyricists** — ranked by number of records (placeholders like
  *παραδοσιακό* / unknown are excluded).
- **Field coverage** — how completely each field is filled in.
- **Record formats** — how many entries mention 45s, 78s, LPs, CDs, etc.

### 🎵 Record view / edit (`/songs/<id>`)
Each song opens as a card laid out like the original archive form —
title, composer, lyricist across the top; lyrics and bibliography on the left;
archive and notes on the right.

- **Edit mode toggle** turns the card into an editable form. Saving updates the
  record, stamps its *updated* time, and re-syncs the search index.
- **Pager** with previous/next and a **jump-to-id** box (type a number, press
  Enter) — clamps to the valid range so it never errors.
- Shows *created* / *updated* timestamps.

### 🔎 Search
- **Live search modal** (opens from the navbar): results appear after **3
  characters**, with full keyboard navigation (↑/↓ to move, Enter to open).
- **Full results page** (`/search`) with **pagination**.
- **Accent- and case-insensitive** Greek full-text search across every text field
  — e.g. `νοσταλγια` matches *Νοσταλγία*. Powered by SQLite FTS5 with a
  pre-folded index, so it stays fast over the full corpus.

Everything is **responsive** (Bootstrap) and works from phone to wide monitor.

---

## The data

One `song` record has these fields (Greek label → attribute):

| Label | Field | Notes |
|---|---|---|
| ΤΙΤΛΟΣ | `title` | song title |
| ΣΥΝΘΕΤΗΣ | `composer` | |
| ΣΤΙΧΟΥΡΓΟΣ | `lyricist` | |
| ΣΤΙΧΟΙ | `lyrics` | |
| ΑΡΧΕΙΟ | `archive` | provenance / collection |
| ΒΙΒΛΙΟΓΡΑΦΙΑ | `bibliography` | rarely filled |
| ΣΗΜΕΙΩΣΕΙΣ | `notes` | genre, label, catalog no., orchestra… |
| — | `created`, `updated` | change-tracking timestamps |

Source data lives in `files/Τραγούδια.csv` (semicolon-delimited, UTF-8) and is
imported into SQLite. See [SETUP.md](SETUP.md) for how it's loaded.

---

## Tech stack

- **Backend:** Python · Flask (app-factory + blueprint)
- **ORM / DB:** Flask-SQLAlchemy · SQLite with an **FTS5** full-text index
- **Migrations:** Flask-Migrate (Alembic)
- **Frontend:** Jinja templates · Bootstrap 5 (vendored, no CDN) · custom theme
- **Server:** gunicorn (in Docker)

## Project layout

```
app.py            Application factory + config
extensions.py     db (SQLAlchemy) + migrate (Flask-Migrate)
models.py         Song model, accent-folding, search-index sync
views.py          Routes: dashboard, record view/edit, search, live API
stats.py          Dashboard statistics queries
import_csv.py     One-off data loader (CSV → SQLite)
migrations/       Alembic migrations (schema + FTS table + triggers)
templates/        base, dashboard, record, search
static/           style.css + vendored Bootstrap
Dockerfile, docker-compose.yaml, docker/entrypoint.sh
```

---

## Deployment

See **[SETUP.md](SETUP.md)** for full instructions (Docker and local development).
