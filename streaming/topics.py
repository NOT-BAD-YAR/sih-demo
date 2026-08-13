"""Kafka topic definitions + partitioning config.

Single source of truth for topic names, partitions, and the key used for
per-entity ordering. Phase 2 owns this; producer/consumer/monitor consume it.
"""

from __future__ import annotations

from dataclasses import dataclass

TOPICS = {
    "auth-events":     4,
    "file-events":     4,
    "network-events":  4,
    "device-events":   4,
    "privilege-events": 2,
}

EVENT_TYPE_TO_TOPIC: dict[str, str] = {
    # auth-events
    "login": "auth-events",
    "logout": "auth-events",
    "mfa": "auth-events",
    "failure": "auth-events",
    # file-events
    "file_access": "file-events",
    "download": "file-events",
    "upload": "file-events",
    # network-events
    "network_conn": "network-events",
    # device-events
    "usb": "device-events",
    "process": "device-events",
    # privilege-events
    "privilege": "privilege-events",
}

# topic -> which event field drives the partition key (per-entity ordering)
TOPIC_KEY_FIELD: dict[str, str] = {
    "auth-events": "user_id",
    "file-events": "entity_id",
    "network-events": "source_entity",
    "device-events": "entity_id",
    "privilege-events": "user_id",
}


@dataclass(frozen=True)
class Topic:
    name: str
    partitions: int
    replication: int = 1


def topic_defs() -> list[Topic]:
    return [Topic(name, parts) for name, parts in TOPICS.items()]


def topic_for(event_type: str) -> str:
    """Map an event_type to its topic (raises for unknown types)."""
    try:
        return EVENT_TYPE_TO_TOPIC[event_type]
    except KeyError as exc:
        raise KeyError(f"no topic mapped for event_type={event_type!r}") from exc


def partition_key(event: dict) -> bytes:
    """Compute the Kafka key for an event dict using the topic's key field."""
    event_type = event["event_type"]
    topic = topic_for(event_type)
    field = TOPIC_KEY_FIELD[topic]
    value = event.get(field) or event.get("entity_id") or ""
    return value.encode("utf-8")