"""Flask application factory for SimpleDiscography.

Wires up Flask-SQLAlchemy against the SQLite database built by import_csv.py.
Routes and the card UI are added in later steps.
"""

from __future__ import annotations

import os

from flask import Flask

from extensions import db, migrate

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
# Overridable so the database and media can live on mounted volumes in Docker.
DB_PATH = os.environ.get("DISCOGRAPHY_DB", os.path.join(BASE_DIR, "discography.db"))
MEDIA_DIR = os.environ.get("MEDIA_DIR", os.path.join(BASE_DIR, "media"))


def _include_object(obj, name, type_, reflected, compare_to):
    """Keep Alembic autogenerate from touching the FTS5 objects.

    The song_fts virtual table and its shadow tables (song_fts_data,
    song_fts_idx, ...) are created by hand in the migration, not from model
    metadata, so exclude them from diffs to avoid spurious drops.
    """
    if type_ == "table" and name.startswith("song_fts"):
        return False
    return True


def create_app(config: dict | None = None) -> Flask:
    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{DB_PATH}"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    # Wait up to 15s on a locked SQLite file (helps with multiple gunicorn workers).
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"connect_args": {"timeout": 15}}
    # Where audio/image files live (mounted volume in Docker). Used later.
    app.config["MEDIA_DIR"] = MEDIA_DIR
    # Used to sign the session cookie for flash messages. Override in production.
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-discography-key")
    if config:
        app.config.update(config)

    db.init_app(app)
    # render_as_batch: SQLite needs batch mode for ALTER TABLE migrations.
    migrate.init_app(app, db, render_as_batch=True, include_object=_include_object)

    # Import models so their tables register with the metadata.
    from models import Song  # noqa: F401
    from views import main

    app.register_blueprint(main)

    return app


app = create_app()


if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000, debug=True)
