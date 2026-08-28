"""Shared Flask extensions.

Kept in its own module so both ``app`` and ``models`` can import ``db``
without creating a circular import.
"""

from __future__ import annotations

import sqlite3

from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Declarative base enabling SQLAlchemy 2.0 typed models (Mapped[...])."""


db = SQLAlchemy(model_class=Base)
migrate = Migrate()


def _lenient_text(raw: bytes) -> str:
    """Decode a SQLite TEXT value, substituting U+FFFD for undecodable bytes."""
    return raw.decode("utf-8", "replace")


@event.listens_for(Engine, "connect")
def _sqlite_lenient_text_factory(dbapi_connection, connection_record) -> None:
    """Survive rows whose TEXT columns aren't valid UTF-8.

    SQLite stores TEXT as bytes and does not validate them, so a bad write can
    leave a row that the default (strict) decoder refuses, raising
    OperationalError. Because find & replace and field-scoped search both scan
    the whole table, a single such row takes those features down for the entire
    archive rather than just for itself.

    Substituting the replacement character keeps every other row reachable and
    makes the damaged one visible and editable in the UI, so it can be repaired.
    Costs roughly 180ms on a full 56k-row, 7-field scan (0.35s -> 0.53s), which
    is paid only by the operations that scan.
    """
    if isinstance(dbapi_connection, sqlite3.Connection):
        dbapi_connection.text_factory = _lenient_text
