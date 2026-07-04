"""Shared Flask extensions.

Kept in its own module so both ``app`` and ``models`` can import ``db``
without creating a circular import.
"""

from __future__ import annotations

from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Declarative base enabling SQLAlchemy 2.0 typed models (Mapped[...])."""


db = SQLAlchemy(model_class=Base)
migrate = Migrate()
