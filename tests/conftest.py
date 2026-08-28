"""Test fixtures: a throwaway database with the real schema.

The FTS5 tables and their sync triggers are created by the migrations rather
than from model metadata, so the fixture runs the migrations instead of
``create_all()`` — otherwise every full-text assertion would be testing a
schema the application never actually uses.
"""

from __future__ import annotations

import pytest
from flask_migrate import upgrade

from app import create_app
from extensions import db


@pytest.fixture
def app(tmp_path):
    dbfile = tmp_path / "test.db"
    application = create_app({
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{dbfile}",
        "DB_PATH": str(dbfile),
        "TESTING": True,
        "WTF_CSRF_ENABLED": False,
    })
    with application.app_context():
        upgrade()
        yield application
        db.session.remove()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def db_path(app):
    return app.config["DB_PATH"]
