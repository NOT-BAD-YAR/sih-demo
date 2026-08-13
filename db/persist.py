"""Engine consumer persistence — bridge Phase 2 Kafka → Phase 3 Postgres.

`build_persist_handler` returns the handler the EngineConsumer invokes for
each decoded event. It writes the event via the DAO (idempotent
`ON CONFLICT (event_id) DO NOTHING`) so duplicates are absorbed by the
database, not double-applied downstream.
"""

from __future__ import annotations

from typing import Any, Callable

from .conn import connect
from .dao import insert_event

PersistHandler = Callable[[dict], bool]


def build_persist_handler(dsn: str | None = None) -> PersistHandler:
    """Handler that persists each event to raw_events over a fresh connection.

    A fresh connection per event keeps the handler independent of any long-lived
    transaction and matches the at-least-once commit contract of the consumer
    (commit happens after this handler returns).

    Returns True when inserted, False when the event_id was a duplicate.
    """
    def handle(payload: dict) -> bool:
        with connect(dsn) as conn:
            return insert_event(conn, payload)

    return handle


def build_persist_handler_conn(conn: Any) -> PersistHandler:
    """Variant for tests that supply their own connection (single transaction)."""
    def handle(payload: dict) -> bool:
        return insert_event(conn, payload)

    return handle