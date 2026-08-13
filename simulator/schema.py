"""Common Event Schema — every source (simulator, agent) emits this shape.

Phase 1 owns the schema definition + validation. Phase 2 (streaming) and the
analytics engine (Phase 4+) consume this exact contract.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import uuid4, UUID

# --- enums -------------------------------------------------------------------

ENTITY_TYPES = ("user", "device", "server", "app")
EVENT_TYPES = (
    "login", "logout", "file_access", "download", "upload", "network_conn",
    "usb", "process", "privilege", "mfa", "failure",
)
OUTCOMES = ("success", "failure")
SENSITIVITIES = ("public", "internal", "confidential", "restricted")


class Field(str, Enum):
    """Canonical field names (convenience for producers/agents)."""

    EVENT_ID = "event_id"
    TS = "ts"
    INGESTED_AT = "ingested_at"
    ENTITY_TYPE = "entity_type"
    ENTITY_ID = "entity_id"
    USER_ID = "user_id"
    EVENT_TYPE = "event_type"
    ACTOR = "actor"
    SOURCE_ENTITY = "source_entity"
    TARGET_ENTITY = "target_entity"
    PEER_ENTITY = "peer_entity"
    IP = "ip"
    GEO = "geo"
    FILE_PATH = "file_path"
    BYTES = "bytes"
    OUTCOME = "outcome"
    SENSITIVITY = "sensitivity"
    RAW_PAYLOAD = "raw_payload"


def _is_enum(value, allowed: tuple[str, ...]) -> bool:
    return value in allowed


class EventValidationError(ValueError):
    """Raised when a raw payload cannot be normalized into an Event."""


@dataclass
class Event:
    """Normalized security event in the Common Event Schema."""

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
    file_path: str | None = None
    bytes_moved: int = 0
    outcome: str = "success"
    sensitivity: str = "internal"
    raw_payload: dict[str, Any] = field(default_factory=dict)

    @property
    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["ts"] = self.ts.isoformat()
        data["ingested_at"] = self.ingested_at.isoformat()
        data["bytes"] = data.pop("bytes_moved")  # canonical field name from the schema table
        return data

    @property
    def chain(self) -> list[str]:
        """Graph-correlation chain: [actor, source, target, peer]."""
        return [x for x in (self.actor, self.source_entity, self.target_entity, self.peer_entity) if x]

    @property
    def to_json(self) -> str:
        return json.dumps(self.to_dict, sort_keys=True)


def deterministic_event_id(rng) -> str:
    """Deterministic UUIDv4-shaped id from a seeded random.Random.

    Keeps backfill/scenario output reproducible across runs (Phase 1 goal).
    """
    import random

    if not isinstance(rng, random.Random):
        rng = random.Random(rng)
    return str(UUID(int=rng.getrandbits(128), version=4))


def build_event(
    *,
    entity_type: str,
    entity_id: str,
    event_type: str,
    actor: str,
    ts: datetime,
    user_id: str = "",
    source_entity: str = "",
    target_entity: str = "",
    peer_entity: str = "",
    ip: str = "",
    geo: dict[str, Any] | None = None,
    file_path: str | None = None,
    bytes_moved: int = 0,
    outcome: str = "success",
    sensitivity: str = "internal",
    raw_payload: dict[str, Any] | None = None,
    event_id: str | None = None,
    ingested_at: datetime | None = None,
) -> Event:
    """Builder that guarantees a well-formed Event for producers."""
    return Event(
        event_id=event_id or str(uuid4()),
        ts=ts,
        ingested_at=ingested_at or ts,
        entity_type=entity_type,
        entity_id=entity_id,
        user_id=user_id,
        event_type=event_type,
        actor=actor,
        source_entity=source_entity,
        target_entity=target_entity,
        peer_entity=peer_entity,
        ip=ip,
        geo=geo or {},
        file_path=file_path,
        bytes_moved=bytes_moved,
        outcome=outcome,
        sensitivity=sensitivity,
        raw_payload=raw_payload or {},
    )


def validate(event: Event) -> list[str]:
    """Schema validator. Returns a list of problems (empty == valid)."""
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
    if amount := event.bytes_moved:
        if not isinstance(amount, int) or amount < 0:
            problems.append("bytes_moved must be non-negative int")
    if event.outcome not in OUTCOMES:
        problems.append(f"outcome invalid: {event.outcome!r}")
    if event.sensitivity not in SENSITIVITIES:
        problems.append(f"sensitivity invalid: {event.sensitivity!r}")
    if not isinstance(event.geo, dict):
        problems.append("geo must be dict")
    return problems


def is_valid(event: Event) -> bool:
    return not validate(event)


def from_dict(data: dict[str, Any]) -> Event:
    """Deserialize a dict back into an Event (used by consumers/tests)."""
    try:
        return Event(
            event_id=data["event_id"],
            ts=datetime.fromisoformat(data["ts"]),
            ingested_at=datetime.fromisoformat(data["ingested_at"]),
            entity_type=data["entity_type"],
            entity_id=data["entity_id"],
            user_id=data.get("user_id", ""),
            event_type=data["event_type"],
            actor=data["actor"],
            source_entity=data.get("source_entity", ""),
            target_entity=data.get("target_entity", ""),
            peer_entity=data.get("peer_entity", ""),
            ip=data.get("ip", ""),
            geo=data.get("geo", {}),
            file_path=data.get("file_path"),
            bytes_moved=data.get("bytes", 0),
            outcome=data.get("outcome", "success"),
            sensitivity=data.get("sensitivity", "internal"),
            raw_payload=data.get("raw_payload", {}),
        )
    except KeyError as exc:  # required field missing
        raise EventValidationError(f"missing field: {exc.args[0]}") from exc