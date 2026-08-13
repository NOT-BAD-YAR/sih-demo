"""Application-level idempotent processing (dedupe by event_id).

Kafka delivers at-least-once: a redelivered message can reach the consumer
twice. This handler wraps the engine callback and ensures each unique
`event_id` is applied exactly once per process. The durable backstop is the
PostgreSQL `raw_events.event_id UNIQUE` constraint (Phase 3), which protects
against duplicates across process restarts.
"""

from __future__ import annotations

from typing import Callable

Handler = Callable[[dict], None]


class IdempotentHandler:
    """Wrap a handler so duplicate event_ids are rejected (not double-applied).

    Attributes:
        processed: number of unique events forwarded to the inner handler.
        rejected:  number of duplicate event_ids dropped.
    """

    def __init__(self, inner: Handler):
        self._inner = inner
        self._seen: set[str] = set()
        self.processed = 0
        self.rejected = 0

    def __call__(self, event: dict) -> bool:
        event_id = event.get("event_id")
        if event_id is not None:
            if event_id in self._seen:
                self.rejected += 1
                return False
            self._seen.add(event_id)
        self._inner(event)
        self.processed += 1
        return True

    def seen_count(self) -> int:
        return len(self._seen)