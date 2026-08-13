"""UEBA storage package (Phase 3 — PostgreSQL).

Owns: Alembic migrations (schema), seed (org + accounts), DAO (typed
repository functions used by the engine and later the API). Later phases
import this package, never copy its code.
"""

from db.conn import connect  # noqa: F401

__all__ = ["connect"]