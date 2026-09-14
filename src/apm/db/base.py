"""Declarative base + shared column mixins for all ORM models.

Concrete tables (spec §54) are added incrementally per milestone (M2/M3+). Keeping
the base isolated lets Alembic autogenerate against a stable metadata object.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Root of all ORM models. Import models before running Alembic autogenerate."""

    # All datetimes are timezone-aware (timestamptz) — the whole system works in UTC
    # and mixing naive/aware values with asyncpg raises. This makes it uniform.
    type_annotation_map = {dt.datetime: DateTime(timezone=True)}


class TimestampMixin:
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


def new_uuid() -> str:
    return str(uuid.uuid4())
