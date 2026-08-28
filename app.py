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

    Every archive has an FTS5 companion (song_fts, disc45_fts, disc78_fts,
    bio_fts) plus its shadow tables (..._data, ..._idx, ..._config,
    ..._docsize). All are created by hand in the migrations, not from model
    metadata, so exclude them from diffs to avoid spurious drops.
    """
    if type_ == "table" and "_fts" in name:
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
    # Absolute path to the SQLite file (used by the admin download).
    app.config["DB_PATH"] = DB_PATH
    # Password gating the /admin area. Unset => admin is disabled (503).
    app.config["ADMIN_PASSWORD"] = os.environ.get("ADMIN_PASSWORD")
    # Allow large uploads in the admin importer (CSV or a full .db restore).
    app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024 * 1024
    # Used to sign the session cookie (flash messages, admin login). Override in production.
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-discography-key")
    if config:
        app.config.update(config)

    db.init_app(app)
    # render_as_batch: SQLite needs batch mode for ALTER TABLE migrations.
    migrate.init_app(app, db, render_as_batch=True, include_object=_include_object)

    # Import models so their tables register with the metadata.
    from models import Biography, Disc45, Disc78, Song  # noqa: F401
    from views import DatasetConverter, main

    # Must be registered before the blueprint: the dataset-scoped URL rules use
    # the "ds" converter, and rules are compiled as the blueprint is registered.
    app.url_map.converters["ds"] = DatasetConverter
    app.register_blueprint(main)

    return app


app = create_app()


if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000, debug=True)
