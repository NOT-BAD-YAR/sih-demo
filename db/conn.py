"""Database connection helpers.

Single place that resolves the Postgres DSN from the shared analytics.Config
(which reads .env). Normalizes away SQLAlchemy driver prefixes so psycopg2
and SQLAlchemy both work with the same DSN string.
"""

from __future__ import annotations

from typing import Any

import psycopg2
import psycopg2.extras
from psycopg2.extensions import connection as PgConnection

from analytics.config import Config


def normalize_dsn(dsn: str) -> str:
    """Strip an SQLAlchemy driver prefix (`postgresql+psycopg2://`)."""
    if dsn.startswith("postgresql+psycopg2://") or dsn.startswith("postgres+psycopg2://"):
        return dsn.replace("+psycopg2", "", 1)
    return dsn


def connect(dsn: str | None = None) -> PgConnection:
    """Open a psycopg2 connection using the given DSN (or Config default)."""
    dsn = normalize_dsn(dsn or Config.from_env().postgres_dsn)
    conn = psycopg2.connect(dsn)
    conn.autocommit = False
    return conn


def dict_cursor(conn: PgConnection) -> Any:
    """Return a psycopg2 RealDictCursor-bound cursor for read helpers."""
    return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)