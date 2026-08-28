# Multi-archive datasets — design

**Date:** 2026-08-28
**Status:** approved

## Goal

Add three more archives alongside the existing Τραγούδια catalogue, each with its
own page, reachable from a sidebar:

| Dataset | Source CSV | Records |
|---|---|---|
| 45άρια (ΜΑΝΙΑΤΗ) | `45άρια (ΜΑΝΙΑΤΗ).csv` | 37,750 |
| 78άρια (ΜΑΝΙΑΤΗ) | `78άρια (ΜΑΝΙΑΤΗ).csv` | 24,513 |
| Βιογραφίες | `Βιογραφίες.csv` | 1,067 |

Each new page has full parity with the existing Τραγούδια pages: dashboard,
browse/edit/delete, add-new, search, and find & replace.

## Decisions

1. **Navigation** — persistent collapsible left sidebar listing the four
   datasets. The navbar keeps the per-dataset actions, scoped to the active
   dataset.
2. **45άρια / 78άρια storage** — two separate tables (`disc45`, `disc78`), not
   one table with a format discriminator. Shared behaviour lives in a mixin and
   a dataset registry so this costs no duplicated route or template code.
3. **Capabilities** — full parity (browse/edit/delete, add, search, find &
   replace) on all three new datasets.
4. **Search scope** — defaults to the active dataset, with a scope selector to
   widen to all four. Cross-dataset results are grouped by dataset.
5. **Dashboards** — one per dataset.
6. **Visual style** — existing warm vinyl palette, one accent colour per
   dataset (oxblood 45άρια, teal 78άρια, slate-blue Βιογραφίες). Field order and
   labels follow the Access forms exactly.
7. **Admin import** — per-dataset CSV import; `.db` restore extended to all
   four tables.

## Data model

`models.py` gains an `ArchiveRecord` mixin carrying what all four tables share:
`search_blob`, `created`, `updated`, `build_search_blob()`, `to_dict()`, and the
`before_insert` / `before_update` search-blob sync.

| Model | Table | Primary key |
|---|---|---|
| `Song` | `song` | existing, unchanged |
| `Disc45` | `disc45` | source `Αναγνωριστικό` (unique, gaps preserved) |
| `Disc78` | `disc78` | source `Αναγνωριστικό` (unique, gaps preserved) |
| `Biography` | `biography` | autoincrement in CSV row order (no source id) |

`Disc45` and `Disc78` have identical columns:

| CSV header | Attribute | 45άρια label | 78άρια label |
|---|---|---|---|
| ΕΤΑΙΡΕΙΑ | `company` | ΕΤΑΙΡΕΙΑ | ΕΤΑΙΡΕΙΑ |
| ΑΡΙΘΜΟΣ ΔΙΣΚΟΥ | `disc_number` | ΑΡΙΘΜΟΣ ΔΙΣΚΟΥ | ΑΡΙΘΜΟΣ ΔΙΣΚΟΥ |
| ΤΙΤΛΟΣ ΤΡΑΓΟΥΔΙΟΥ | `title` | ΤΙΤΛΟΣ ΤΡΑΓΟΥΔΙΟΥ | ΤΙΤΛΟΣ ΤΡΑΓΟΥΔΙΟΥ |
| ΤΡΑΓΟΥΔ / ΣΥΝΘΕΤ / ΣΤΙΧΟΥ | `creators` | ΔΗΜΙΟΥΡΓΟΙ | ΤΡΑΓΟΥΔ / ΣΥΝΘΕΤ / ΣΤΙΧ |
| ΕΤΟΣ | `year` | ΕΤΟΣ | ΕΤΟΣ |
| ΕΙΔΟΣ | `genre` | ΕΙΔΟΣ | ΕΙΔΟΣ |
| ΡΥΘΜΟΣ | `rhythm` | ΡΥΘΜΟΣ | ΡΥΘΜΟΣ |

The two forms label the same CSV column differently (ΔΗΜΙΟΥΡΓΟΙ vs
ΤΡΑΓΟΥΔ / ΣΥΝΘΕΤ / ΣΤΙΧ); the registry declares labels per dataset so both
render as in Access.

`Biography` columns: `name` (ΟΝΟΜΑΤΕΠΩΝΥΜΟ), `place_dates` (ΤΟΠΟΣ-ΧΡΟΝΟΛΟΓΙΕΣ),
`capacity` (ΙΔΙΟΤΗΤΑ), `details` (ΣΤΟΙΧΕΙΑ), `discography` (ΔΙΣΚΟΓΡΑΦΙΑ).

`year` is stored as **text**, not an integer: it is free-form and only 62.8%
filled on the 78s. Dashboards parse a leading 4-digit year where possible and
report the unparseable count.

### fold(): NFD → NFKD

`fold()` switches from `NFD` to `NFKD` normalization so the MICRO SIGN (U+00B5)
folds to Greek μ (U+03BC). The source data mixes them: `Ρεµπέτικο` (2,619) and
`Ρεμπέτικο` (1,770) are different strings today, as are
`Ζεϊµπέκικος`/`Ζεϊμπέκικος` and `Δηµοτικό`/`Δημοτικό`.

This also fixes existing data: 158 `song` rows (18 `composer`, 57 `notes`, 83
`archive`) contain a micro sign and are currently unfindable by a search typed
with Greek μ. The migration rebuilds `song.search_blob` once so they become
searchable.

Stored field values stay byte-exact; only the derived search blob is folded.

## Dataset registry

New `datasets.py`. Each dataset is one descriptor declaring:

- `key` — URL slug (`songs`, `45`, `78`, `bios`)
- `model` — the SQLAlchemy model
- `title` — Greek page/sidebar title
- `theme` — CSS accent class
- `csv_columns` — CSV header → attribute
- `labels` — attribute → Greek display label (per dataset)
- `layout` — ordered field layout for the record card: widget (input or
  textarea), row count, and column placement
- `list_fields` — the fields shown in search-result rows

One generic set of routes reads the registry and serves all four datasets.

Routes use a constrained converter, `/<any(songs,45,78,bios):ds>/…`, so nothing
shadows `/search` or `/admin/*`. **Existing `/songs/...` URLs keep working
unchanged.**

## Views

`views.py` (570 lines) becomes a package, since four datasets at full parity
would push a single module past 1,200 lines:

```
views/__init__.py   blueprint + shared helpers
views/records.py    dashboard, browse, edit, add, delete, goto
views/search.py     live search API + results page
views/replace.py    find & replace
views/admin.py      login, import, download
```

Pure restructuring — no behaviour change to existing pages.

## Search

One FTS5 virtual table plus three triggers per new table (`disc45_fts`,
`disc78_fts`, `bio_fts`), mirroring the existing `song_fts` pattern.

Navbar search defaults to the active dataset with a scope selector to widen to
all four; cross-dataset results come from a UNION across the four FTS tables,
grouped by dataset with per-group counts.

`app.py`'s `_include_object` currently excludes only `song_fts*` from Alembic
autogenerate; it widens to any `*_fts*` table so autogenerate never tries to
drop the new virtual tables.

## Import / admin

- `dataio.py` generalizes to `read_rows(dataset, stream)` and
  `replace_all(dataset, rows)`.
- `import_csv.py` takes a dataset argument; the three CSVs are copied into
  `files/`.
- The admin importer gets a dataset selector, so any of the four CSVs can be
  re-uploaded independently, replacing only its own table.
- `validate_sqlite_db()` and `replace_songs_from_db()` currently know only about
  `song`. Restoring a `.db` today would silently leave the new tables untouched
  while replacing songs. Both extend to all four tables.

## Migration

One Alembic revision:

1. Create `disc45`, `disc78`, `biography`.
2. Create `disc45_fts`, `disc78_fts`, `bio_fts` and their 9 sync triggers.
3. Rebuild `song.search_blob` under NFKD and reindex `song_fts`.

Note: a batch ALTER on `song` drops the `song_ai`/`song_ad`/`song_au` triggers;
this revision does not alter `song`, so they survive. Any future batch alter
must recreate them.

## Testing

Add `pytest`. Coverage focused on what breaks silently:

- `fold()` under NFKD, including the micro-sign case
- CSV parsing per dataset, including quoted multi-line fields and the BOM
- FTS trigger sync after insert, update, and delete
- gap-tolerant prev/next over non-contiguous primary keys
- per-dataset import replacing only its own table

## Out of scope

- **Linking Βιογραφίες to song records.** `ΔΙΣΚΟΓΡΑΦΙΑ` is free prose
  ("1967 - Αναμνήσεις"), not record IDs, so there is nothing reliable to join
  on. It is also filled for only 21 of 1,067 rows.
- **Deduplicating the 2,581 `ΕΤΑΙΡΕΙΑ` label variants.**
