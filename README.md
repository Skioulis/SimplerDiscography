# SimpleDiscography — Δισκογραφικό Αρχείο

A web application for browsing, searching, and editing **four Greek discography
archives** — composers, lyricists, lyrics, provenance, pressing details and
biographies for rebetiko / laïkó and related repertoire, from the 78 rpm era to
today.

| Archive | Route | Records | Source |
|---|---|---|---|
| **Τραγούδια** | `/songs/` | ~56,000 | `files/Τραγούδια.csv` |
| **45άρια (ΜΑΝΙΑΤΗ)** | `/45/` | 37,750 | `files/45άρια (ΜΑΝΙΑΤΗ).csv` |
| **78άρια (ΜΑΝΙΑΤΗ)** | `/78/` | 24,513 | `files/78άρια (ΜΑΝΙΑΤΗ).csv` |
| **Βιογραφίες** | `/bios/` | 1,067 | `files/Βιογραφίες.csv` |

A left sidebar switches between them. Each archive gets the same set of
features — dashboard, browse/edit/delete, add, search, find & replace — and its
own accent colour, so it's clear at a glance which one you're in.

The interface is in Greek, matching the source archives.

---

## What it does

### 📊 Dashboards (`/`, `/45/`, `/78/`, `/bios/`)
An at-a-glance overview of the whole collection:

- **Headline figures** — total songs, distinct composers and lyricists, and the
  time span covered.
- **Recordings by decade** — a bar chart of the collection's shape (the 1960s–70s
  peak stands out), derived from year references in the archive/notes fields.
- **Top composers & lyricists** — ranked by number of records (placeholders like
  *παραδοσιακό* / unknown are excluded).
- **Field coverage** — how completely each field is filled in.
- **Record formats** — how many entries mention 45s, 78s, LPs, CDs, etc.

### 🎵 Record view / edit (`/<archive>/<id>`)
Each song opens as a card laid out like the original archive form —
title, composer, lyricist across the top; lyrics and bibliography on the left;
archive and notes on the right.

- **Edit mode toggle** turns the card into an editable form. Saving updates the
  record, stamps its *updated* time, and re-syncs the search index.
- **Pager** with previous/next and a **jump-to-id** box (type a number, press
  Enter) — clamps to the valid range so it never errors.
- Shows *created* / *updated* timestamps.

### 🛠 Admin (`/admin`, password-gated)
- **Εισαγωγή CSV** — replace one archive from its CSV.
- **Λήψη βάσης** — download the SQLite file.
- **Επαναφορά βάσης** — restore every archive from an uploaded `.db`, with a
  review screen showing the row-count change per archive and an automatic
  snapshot of the current database taken first.

### 🔎 Search
- **Live search modal** (opens from the navbar): results appear after **3
  characters**, with full keyboard navigation (↑/↓ to move, Enter to open).
- **Scoped to the archive you're in**, with a selector to widen the search to
  **all four** — cross-archive results are grouped per archive with counts.
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

### 45άρια / 78άρια (`disc45`, `disc78`)

Both exports have identical columns, and each is kept in its own table so either
can gain fields later without conditionals. The two Access forms label the
creators column differently, and the app follows each one:

| Label (45άρια) | Label (78άρια) | Field | Notes |
|---|---|---|---|
| ΕΤΑΙΡΕΙΑ | ΕΤΑΙΡΕΙΑ | `company` | label; often two lines |
| ΑΡΙΘΜΟΣ ΔΙΣΚΟΥ | ΑΡΙΘΜΟΣ ΔΙΣΚΟΥ | `disc_number` | catalogue number |
| ΤΙΤΛΟΣ ΤΡΑΓΟΥΔΙΟΥ | ΤΙΤΛΟΣ ΤΡΑΓΟΥΔΙΟΥ | `title` | |
| ΔΗΜΙΟΥΡΓΟΙ | ΤΡΑΓΟΥΔ / ΣΥΝΘΕΤ / ΣΤΙΧ | `creators` | performer / composer / lyricist |
| ΕΤΟΣ | ΕΤΟΣ | `year` | **free text**, not an integer |
| ΕΙΔΟΣ | ΕΙΔΟΣ | `genre` | |
| ΡΥΘΜΟΣ | ΡΥΘΜΟΣ | `rhythm` | |

`id` carries the source `Αναγνωριστικό` rather than being autoincremented: the
exports are numbered with gaps (the 45s run 1–38947 over 37,750 rows), and
keeping the numbering aligns prev/next with the original archive.

### Βιογραφίες (`biography`)

| Label | Field | Notes |
|---|---|---|
| ΟΝΟΜΑΤΕΠΩΝΥΜΟ | `name` | |
| ΤΟΠΟΣ-ΧΡΟΝΟΛΟΓΙΕΣ | `place_dates` | birth/death place and dates |
| ΙΔΙΟΤΗΤΑ | `capacity` | τραγουδιστής, συνθέτης, συγκρότημα… |
| ΣΤΟΙΧΕΙΑ | `details` | long biographical prose |
| ΔΙΣΚΟΓΡΑΦΙΑ | `discography` | filled for only 21 of 1,067 records |

The export carries no id column, so rows are numbered in file order.

### A note on μ

The archives mix **U+00B5 MICRO SIGN** with **U+03BC GREEK SMALL LETTER MU**, so
`Ρεµπέτικο` and `Ρεμπέτικο` are different strings on disk (2,619 and 1,770 rows
in the 78s). Stored values are left byte-exact, but:

- `fold()` normalizes with **NFKD**, so both spellings answer one search;
- dashboard rankings group on a normalized key, so they count as one value.

### Loading the data

Source data lives in `files/` (semicolon-delimited, UTF-8 with BOM) and is
imported into SQLite. Archives must be named explicitly, because importing one
discards any edits made through the UI since its CSV was exported:

```bash
python import_csv.py 45 78 bios      # the three ΜΑΝΙΑΤΗ / Βιογραφίες archives
python import_csv.py all             # every archive, songs included
```

The admin area (`/admin/import`) does the same per archive from the browser, and
also accepts a full `.db` restore covering all four. See [SETUP.md](SETUP.md).

---

## Tech stack

- **Backend:** Python · Flask (app-factory + blueprint)
- **ORM / DB:** Flask-SQLAlchemy · SQLite with an **FTS5** full-text index
- **Migrations:** Flask-Migrate (Alembic)
- **Frontend:** Jinja templates · Bootstrap 5 (vendored, no CDN) · custom theme
- **Tests:** pytest (`python -m pytest`)
- **Server:** gunicorn (in Docker)

## Project layout

```
app.py            Application factory + config
extensions.py     db + migrate, and the lenient SQLite text decoder
models.py         The four archive models, accent-folding, search-index sync
datasets.py       Dataset registry: labels, field layout, theme, routes
views/            Blueprint, split by area
  __init__.py       shared helpers + the dataset URL converter
  records.py        dashboard, browse, edit, add, delete, goto
  search.py         live search API + results page
  replace.py        find & replace
  admin.py          login, per-archive import, download
stats.py          Dashboard statistics, one builder per archive kind
import_csv.py     Data loader (CSV → SQLite), per archive
dataio.py         Shared CSV / .db import logic
migrations/       Alembic migrations (schema + FTS tables + triggers)
templates/        base (sidebar/nav), dashboards, record, search, replace
static/           style.css + vendored Bootstrap
tests/            pytest suite (run: python -m pytest)
Dockerfile, docker-compose.yaml, docker/entrypoint.sh
```

---

## Deployment

- **[SETUP.md](SETUP.md)** — first-time install (Docker and local development).
- **[UPDATING.md](UPDATING.md)** — rolling out code changes, migrations, and rollback.
