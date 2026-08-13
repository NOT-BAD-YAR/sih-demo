"""Kafka producer.

Single "ship an event" path shared by the simulator and the Windows agent.
Idempotent producer + acks=all for at-least-once safety; application-level
dedupe happens downstream on event_id (Phase 3 DB unique constraint).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from confluent_kafka import Producer

from .topics import partition_key, topic_for


class EventProducer:
    """Thin wrapper over confluent-kafka Producer for UEBA events."""

    def __init__(self, bootstrap: str, topic: str):
        self._topic = topic
        self._conf = {
            "bootstrap.servers": bootstrap,
            "acks": "all",
            "enable.idempotence": True,
            "linger.ms": 10,
            "retries": 5,
        }
        self._producer = Producer(self._conf)

    def send(self, event: dict) -> None:
        """Deliver a single normalized event (dict) to its topic.

        Uses `event.delivery_callback` semantics via poll() inside flush();
        raises RuntimeError if the broker rejects the message (delivery failed).
        """
        self._producer.produce(
            topic=self._topic,
            key=partition_key(event),
            value=json.dumps(event).encode("utf-8"),
            callback=self._delivery_report,
        )
        self._producer.poll(0)

    def flush(self, timeout: float = 15.0) -> int:
        """Block until all buffered messages delivered; returns remaining count."""
        return self._producer.flush(timeout)

    def close(self) -> None:
        try:
            self.flush()
        finally:
            self._producer.flush()

    @staticmethod
    def _delivery_report(err, msg) -> None:
        if err is not None:
            raise RuntimeError(f"delivery failed: {err}")


def normalize_payload(event) -> dict[str, Any]:
    """Convert an Event object (simulator/agent) into a ship-ready dict.

    Sets ingested_at if missing.
    """
    data = event.to_dict
    if "ingested_at" not in data:
        data["ingested_at"] = datetime.now(timezone.utc).isoformat()
    return data