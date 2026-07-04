# Updating

What to do when the code changes and you need to roll the update out. For a
first-time install see [SETUP.md](SETUP.md); for what the app does see
[README.md](README.md).

> **Before any production update, back up the database** (see
> [SETUP.md → Backing up the database](SETUP.md#backing-up-the-database)).
> Your data lives on the `db_data` volume and is **not** touched by rebuilds,
> but a backup is cheap insurance before running migrations.

---

## TL;DR

**Docker:**
```bash
git pull
docker compose up -d --build      # rebuild image + restart; migrations run on boot
```

**Local:**
```bash
git pull
pip install -r requirements.txt   # in your virtualenv
flask --app app db upgrade        # apply any new migrations
# restart the server
```

The two volumes (`db_data`, `media_data`) persist, so your songs and media
survive the update.

---

## Docker deployments

```bash
git pull
docker compose up -d --build
```

- `--build` rebuilds the image with the new code and dependencies.
- On container start the entrypoint automatically runs `flask db upgrade`, so
  **new migrations are applied for you**. The data import only runs if the
  database is empty, so existing data is never re-imported.
- Volumes are preserved across `up`/`down`/rebuilds — you do **not** lose data.
- Watch it come up: `docker compose logs -f`.

Only `docker compose down -v` deletes the volumes (full wipe — avoid unless you
mean it).

---

## Local development

```bash
git pull
source .venv/bin/activate
pip install -r requirements.txt   # only needed if requirements.txt changed
flask --app app db upgrade        # only needed if there are new migrations
```

Then restart however you run it (`flask --app app run`, `python app.py`, or
`gunicorn app:app`).

> Templates and CSS are **cached** unless the dev server runs in debug mode
> (`python app.py`). After changing templates/static with `flask run` or
> gunicorn, **restart the process** to see the changes.

---

## What changed? → What to do

| What changed | Docker | Local |
|---|---|---|
| Routes / Python (`views.py`, `stats.py`, …) | rebuild + restart | restart server |
| Templates / CSS / vendored assets | rebuild + restart | restart (or use debug reload) |
| `requirements.txt` (dependencies) | rebuild (`--build`) | `pip install -r requirements.txt` |
| `models.py` (schema) | generate a migration first (below), then rebuild | generate a migration, then `flask db upgrade` |
| `files/Τραγούδια.csv` (source data) | rebuild, then `docker compose exec web python import_csv.py` | `python import_csv.py` |

The image bundles the CSV, so a data change requires a rebuild before re-importing
inside the container.

---

## Database schema changes

Whenever `models.py` changes, create and commit a migration **during
development**, then deploy — the deploy applies it automatically (Docker) or via
`flask db upgrade` (local).

```bash
flask --app app db migrate -m "describe the change"
# review the generated file in migrations/versions/ ...
flask --app app db upgrade        # test it locally
git add migrations/versions/<new_file>.py && git commit
```

> ⚠️ **FTS trigger caveat.** On SQLite, a migration that alters the `song` table
> runs in *batch* mode, which rebuilds the table and **drops the full-text search
> triggers** (`song_ai` / `song_ad` / `song_au`). Any such migration must
> recreate them in **both** `upgrade()` and `downgrade()` — see
> `migrations/versions/*_add_created_and_updated_timestamps.py` for the pattern.
> If search silently stops updating after a schema change, this is why; re-run
> `python import_csv.py` to rebuild the index and fix the migration.

---

## Rolling back

**Code** — check out the previous commit/tag and redeploy:
```bash
git checkout <previous-tag>
docker compose up -d --build      # or restart locally
```

**A migration** — downgrade one step before deploying older code that expects the
old schema:
```bash
flask --app app db downgrade -1                      # local
docker compose exec web flask db downgrade -1        # in the container
```

Migrations are reversible only as far as their `downgrade()` is correct — review
before relying on it, and keep a database backup.

---

## Quick checklist

- [ ] Backed up the database
- [ ] `git pull`
- [ ] Dependencies installed / image rebuilt (if `requirements.txt` changed)
- [ ] Migrations generated & committed (if `models.py` changed)
- [ ] `flask db upgrade` ran (auto in Docker, manual locally)
- [ ] Data re-imported (only if the CSV changed)
- [ ] Server / container restarted
- [ ] Smoke-tested: dashboard loads, a record opens, search returns results
