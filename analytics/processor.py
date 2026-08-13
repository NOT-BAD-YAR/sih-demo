"""Event Processor — 4.1 normalize/validate ingress.

Turns wire payloads (Common Event Schema dicts) into a frozen
`NormalizedEvent` used by the feature engine and rule detectors. Invalid
events are dropped (returned as None) and never crash the pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from simulator.schema import (
    ENTITY_TYPES,
    EVENT_TYPES,
    OUTCOMES,
    SENSITIVITIES,
    from_dict,
)


@dataclass(frozen=True)
class NormalizedEvent:
    """Validated, schema-conformant event for the analytics engine.

    Mirrors the Common Event Schema (Phase 1). `bytes_moved` is the canonical
    `bytes` field renamed to avoid clashing with the builtin.
    """

    event_id: str
    ts: datetime
    ingested_at: datetime
    entity_type: str
    entity_id: str
    user_id: str
    event_type: str
    actor: str
    source_entity: str
    target_entity: str
    peer_entity: str
    ip: str
    geo: dict[str, Any]
    file_path: str | None
    bytes_moved: int
    outcome: str
    sensitivity: str

    @property
    def chain(self) -> list[str]:
        """Graph-correlation chain: [actor, source, target, peer]."""
        return [x for x in (self.actor, self.source_entity, self.target_entity, self.peer_entity) if x]


_MAX_CLOCK_SKEW = 5 * 60  # allow up to 5 minutes future skew


def validate(raw: dict[str, Any]) -> NormalizedEvent | None:
    """Schema-check a wire payload.

    Required fields present, enums valid, ts sane (parseable + not wildly in
    the future). Returns a NormalizedEvent on success or None when invalid —
    callers log the drop, never raise.
    """
    try:
        event = from_dict(raw)
    except Exception:
        return None

    problems: list[str] = []
    if not event.event_id:
        problems.append("event_id required")
    if not isinstance(event.ts, datetime):
        problems.append("ts must be datetime")
    if not isinstance(event.ingested_at, datetime):
        problems.append("ingested_at must be datetime")
    if event.entity_type not in ENTITY_TYPES:
        problems.append(f"entity_type invalid: {event.entity_type!r}")
    if not event.entity_id:
        problems.append("entity_id required")
    if event.event_type not in EVENT_TYPES:
        problems.append(f"event_type invalid: {event.event_type!r}")
    if not event.actor:
        problems.append("actor required")
    if event.outcome not in OUTCOMES:
        problems.append(f"outcome invalid: {event.outcome!r}")
    if event.sensitivity not in SENSITIVITIES:
        problems.append(f"sensitivity invalid: {event.sensitivity!r}")
    if event.bytes_moved < 0:
        problems.append("bytes_moved must be non-negative")

    if event.ts.tzinfo is None:
        problems.append("ts must be timezone-aware")
    elif (event.ts - datetime.now(timezone.utc)).total_seconds() > _MAX_CLOCK_SKEW:
        problems.append("ts too far in the future")

    if problems:
        return None

    return NormalizedEvent(
        event_id=event.event_id,
        ts=event.ts,
        ingested_at=event.ingested_at,
        entity_type=event.entity_type,
        entity_id=event.entity_id,
        user_id=event.user_id,
        event_type=event.event_type,
        actor=event.actor,
        source_entity=event.source_entity,
        target_entity=event.target_entity,
        peer_entity=event.peer_entity,
        ip=event.ip,
        geo=event.geo,
        file_path=event.file_path,
        bytes_moved=event.bytes_moved,
        outcome=event.outcome,
        sensitivity=event.sensitivity,
    )


def resolve_user(ref: NormalizedEvent, device_owner: dict[str, str] | None = None) -> str:
    """Resolve the human actor behind an event.

    - user events: the event's user_id already identifies the person.
    - device/server events: map the acting entity to its owning employee via
      the provided `device_owner` mapping ({entity_id -> emp_id}). Without a
      mapping the raw user_id (or empty string) is returned, never an error —
      the cold-start path tolerates unknown actors.
    """
    if ref.user_id:
        return ref.user_id
    if ref.entity_type == "user":
        return ref.actor
    if ref.entity_type in ("device", "server", "app") and device_owner:
        return device_owner.get(ref.entity_id, "")
    return ""
