# Setup & Deployment

Two ways to run SimpleDiscography: **Docker** (recommended for deployment) or a
**local Python environment** (for development). See [README.md](README.md) for
what the app does.

> **Data note:** the app seeds itself from `files/Τραγούδια.csv`. That file must
> be present in the project before building the image or running the local
> importer.

---

## Get the code

Clone the repository and enter it:

```bash
git clone https://github.com/Skioulis/SimplerDiscography.git
cd SimplerDiscography
```

Prefer SSH? `git clone git@github.com:Skioulis/SimplerDiscography.git`

---

## Option A — Docker (recommended)

### Prerequisites
- Docker Engine + the Docker Compose plugin (`docker compose`).

### 1. Set the secrets
Create a `.env` file next to `docker-compose.yaml`:

```env
SECRET_KEY=replace-with-a-long-random-string
ADMIN_PASSWORD=choose-an-admin-password
```

(Generate a secret key with e.g. `python -c "import secrets; print(secrets.token_hex(32))"`.)

`ADMIN_PASSWORD` gates the **/admin** area (CSV import + database download). If it's
left unset, the admin area is disabled (returns 503).

### 2. Build and start

```bash
docker compose up --build        # foreground
docker compose up --build -d     # detached (background)
```

The app is then available at **http://localhost:5000** (and on your LAN at
`http://<this-machine-ip>:5000`).

**On first boot** the container automatically:
1. applies the database migrations (creates the schema on the `db_data` volume), then
2. detects the database is empty and **imports ~56k songs** from the bundled CSV.

On every later start it sees the data is already there and skips straight to
serving — your data persists on the volume.

### 3. Common commands

```bash
docker compose logs -f           # follow logs (watch the first-boot seeding)
docker compose down              # stop and remove the container (volumes KEPT)
docker compose up -d --build     # rebuild after code changes
docker compose down -v           # stop AND delete the volumes (wipes the DB!)
```

---

## Configuration

Set via environment variables (in `.env` or the `environment:` block of
`docker-compose.yaml`):

| Variable | Default (Docker) | Purpose |
|---|---|---|
| `SECRET_KEY` | `please-change-me` | Signs session cookies. **Set this in production.** |
| `ADMIN_PASSWORD` | *(unset → admin disabled)* | Password for the **/admin** area (CSV import + DB download) |
| `DISCOGRAPHY_DB` | `/data/db/discography.db` | Path to the SQLite database file |
| `MEDIA_DIR` | `/data/media` | Directory for audio/image files |
| `WEB_CONCURRENCY` | `3` | Number of gunicorn workers |

### Changing the port
Edit the port mapping in `docker-compose.yaml` (`"HOST:CONTAINER"`), e.g. to serve
on host port 8080:

```yaml
    ports:
      - "8080:5000"
```

---

## Persistent volumes

Two named Docker volumes hold everything that must survive restarts and rebuilds:

| Volume | Mounted at | Contents |
|---|---|---|
| `db_data` | `/data/db` | the SQLite database (`discography.db`) |
| `media_data` | `/data/media` | audio files & images (added later) |

They are **not** deleted by `docker compose down` — only by `docker compose down -v`.

### Adding media files
Drop files onto the `media_data` volume, for example:

```bash
docker cp ./my-audio-folder/. simplediscography:/data/media/
```

`MEDIA_DIR` already points at `/data/media`, ready for when serving is wired up.

### Backing up the database

```bash
docker compose exec web sh -c 'cat "$DISCOGRAPHY_DB"' > backup-$(date +%F).db
```

To restore, stop the app and copy a `.db` file back into the `db_data` volume.

---

## Admin area

Visit **`/admin`** and log in with `ADMIN_PASSWORD`. It has two pages:

- **Εισαγωγή CSV (Import)** — upload a CSV with the same structure (columns
  `ΤΙΤΛΟΣ; ΣΥΝΘΕΤΗΣ; ΣΤΙΧΟΥΡΓΟΣ; ΣΤΙΧΟΙ; ΑΡΧΕΙΟ; ΒΙΒΛΙΟΓΡΑΦΙΑ; ΣΗΜΕΙΩΣΕΙΣ`,
  `;`-delimited, UTF-8). **This replaces all existing data** — a confirmation
  checkbox is required, and the import is transactional (a malformed file leaves
  the database untouched).
- **Λήψη βάσης (Download)** — download a copy of the SQLite database to your PC.

If `ADMIN_PASSWORD` is not set, `/admin` returns 503 (feature disabled).

---

## Option B — Local development

### Prerequisites
- Python 3.12+ (developed on 3.14; the Docker image uses 3.12).

### 1. Create a virtualenv and install dependencies

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Create the schema and load the data

```bash
flask --app app db upgrade     # create tables + FTS index + triggers
python import_csv.py           # import files/Τραγούδια.csv (~56k rows)
```

This creates `discography.db` in the project root. To point elsewhere, set
`DISCOGRAPHY_DB=/path/to/discography.db` before running both commands.

### 3. Run

```bash
flask --app app run --host 0.0.0.0 --port 5000     # dev server
# or
python app.py                                      # dev server with debug=True
# or, like production:
gunicorn --bind 0.0.0.0:5000 --workers 3 app:app
```

> `python app.py` runs with `debug=True`, which exposes the interactive debugger.
> Don't use it on an untrusted network — use `flask run` (no debug) or gunicorn.

---

## Database migrations

The schema is managed by Flask-Migrate (Alembic). After changing a model:

```bash
flask --app app db migrate -m "describe the change"
# review the generated file in migrations/versions/, then:
flask --app app db upgrade
```

> ⚠️ **SQLite + triggers gotcha:** a migration that alters the `song` table runs
> in *batch* mode, which rebuilds the table and **drops the FTS sync triggers**.
> Any such migration must recreate the `song_ai` / `song_ad` / `song_au` triggers
> in both `upgrade()` and `downgrade()` (see the timestamp migration for the
> pattern), or full-text search will silently stop staying in sync.

In Docker, `flask db upgrade` runs automatically on container start, so deploying
a new image with new migrations applies them for you.

### Re-importing / reseeding
`import_csv.py` is idempotent — it clears the `song` table and reloads it, then
rebuilds the search index. Run it again any time the CSV changes:

```bash
python import_csv.py                              # local
docker compose exec web python import_csv.py      # inside the container
```

---

## Troubleshooting

- **Port 5000 already in use** — change the host port in `docker-compose.yaml`, or
  free the port.
- **"song table not found" when importing** — run `flask --app app db upgrade`
  first; the importer only loads data, it doesn't create the schema.
- **Search returns nothing after a schema change** — the FTS triggers were likely
  dropped by a batch migration; re-run the import (`python import_csv.py`) to
  rebuild the index, and fix the migration per the note above.
- **Want a clean slate (Docker)** — `docker compose down -v` deletes the volumes;
  the next `up` re-migrates and re-seeds from scratch.
