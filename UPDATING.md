# Updating

What to do when the code changes and you need to roll the update out. For a
first-time install see [SETUP.md](SETUP.md); for what the app does see
[README.md](README.md).

> **Before any production update, back up the database** (see
> [SETUP.md → Backing up the database](SETUP.md#backing-up-the-database)).
> Your data lives on the `db_data` volume and is **not** touched by rebuilds,
> but a backup is cheap insurance before running migrations.

---

> **Adding a new archive?** The source CSVs are **not in git** (`files/` is
> gitignored), so `git pull` alone will not bring them. See
> [Releases that add an archive](#releases-that-add-an-archive) below.

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
  **new migrations are applied for you**.
- It then checks **each archive separately** and imports only the ones that are
  empty *and* whose CSV is in the image. Archives that already hold rows are
  never re-imported, so your edits are safe; an archive added by a new release
  is seeded on the first boot after the upgrade. The log names each decision:

  ```
  [entrypoint]   Τραγούδια: 56171 rows, skipping.
  [entrypoint]   45άρια (ΜΑΝΙΑΤΗ): empty, will import.
  ```
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
| A CSV in `files/` (source data) | copy the CSV in, rebuild, then `docker compose exec web python import_csv.py <archive>` | `python import_csv.py <archive>` |
| A **new** archive in a release | see [below](#releases-that-add-an-archive) | copy CSV in, `flask db upgrade`, `python import_csv.py <archive>` |

The image bundles the CSVs from the build context, so a data change requires a
rebuild before re-importing inside the container.

Archive keys for `import_csv.py` are `songs`, `45`, `78`, `bios` — or `all` for
every one of them. **Naming is required**: importing an archive discards any
edits made through the UI since its CSV was exported, so the target is never
inferred.

---

## Releases that add an archive

`files/` is gitignored, so the CSVs live only on the machines you put them on.
A release that adds an archive therefore needs the file copied to the server
**before** the rebuild — otherwise the archive is created empty and the log says
so.

This applied to the release adding **45άρια**, **78άρια** and **Βιογραφίες**:

```bash
# 1. From your machine: copy the new CSVs into the server's checkout.
scp files/45άρια\ \(ΜΑΝΙΑΤΗ\).csv \
    files/78άρια\ \(ΜΑΝΙΑΤΗ\).csv \
    files/Βιογραφίες.csv \
    user@server:/path/to/SimplerDiscography/files/

# 2. On the server: back up, pull, rebuild.
ssh user@server
cd /path/to/SimplerDiscography
docker compose exec web python -c "import shutil,datetime; shutil.copy('/data/db/discography.db', f'/data/db/backup-{datetime.date.today()}.db')"
git pull
docker compose up -d --build

# 3. Watch it seed the new archives (roughly 20s for ~63k rows).
docker compose logs -f
```

Expect this in the log:

```
[entrypoint] Applying database migrations...
    rebuilt search_blob for N of M song rows
[entrypoint]   Τραγούδια: 56171 rows, skipping.
[entrypoint]   45άρια (ΜΑΝΙΑΤΗ): empty, will import.
[entrypoint]   78άρια (ΜΑΝΙΑΤΗ): empty, will import.
[entrypoint]   Βιογραφίες: empty, will import.
[entrypoint] Importing archives: 45 78 bios
```

**Forgot to copy the CSVs?** Nothing breaks — the container boots and logs which
archives it skipped. Either copy them in and rebuild, or upload each CSV through
**/admin/import** (pick the target archive first), which needs no shell access.

**Verify:** open `/45/`, `/78/` and `/bios/` — the record counts should read
37,750 / 24,513 / 1,067 — and confirm `/songs/` still shows your own total.

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
> that archive's import (e.g. `python import_csv.py songs`) to rebuild the index,
> and fix the migration.

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
- [ ] New archive CSVs copied to the server **before** rebuilding
- [ ] Data re-imported (only if a CSV changed) — naming the archive explicitly
- [ ] Server / container restarted
- [ ] Smoke-tested: every archive's dashboard loads, a record opens, search
      returns results, and find & replace finds matches
