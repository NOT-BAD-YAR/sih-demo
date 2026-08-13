"""Kafka consumer — the engine's ingest worker.

At-least-once delivery: offset committed only AFTER the handler processes the
message successfully. Dupes are handled downstream (event_id unique in DB).
For Phase 2 the handler is the persistence callback injected by the runner.
"""

from __future__ import annotations

import json
from typing import Callable, Optional

from confluent_kafka import Consumer, KafkaError, Message

Handler = Callable[[dict], None]


class EngineConsumer:
    def __init__(
        self,
        bootstrap: str,
        group_id: str,
        topics: list[str],
        handler: Handler,
        *,
        auto_offset_reset: str = "earliest",
    ):
        self._conf = {
            "bootstrap.servers": bootstrap,
            "group.id": group_id,
            "auto.offset.reset": auto_offset_reset,
            "enable.auto.commit": False,
            "session.timeout.ms": 6000,
        }
        self._consumer = Consumer(self._conf)
        self._consumer.subscribe(topics)
        self._handler = handler

    def _handle_message(self, msg: Message) -> Optional[dict]:
        """Decode a Kafka message, run the handler, then commit the offset.

        At-least-once contract: the offset is committed only AFTER the handler
        returns without raising. If the handler raises, the offset is left
        uncommitted so the message is redelivered (idempotent DB dedupe covers
        the duplicate application on Phase 3+).
        """
        payload = json.loads(msg.value().decode("utf-8"))
        self._handler(payload)
        self._consumer.commit(asynchronous=False)
        return payload

    def poll_once(self, timeout: float = 1.0) -> Optional[dict]:
        """Poll one message; handle + commit. Returns the handled dict or None."""
        msg: Message = self._consumer.poll(timeout)
        if msg is None:
            return None
        if msg.error():
            if msg.error().code() == KafkaError._PARTITION_EOF:
                return None
            raise RuntimeError(f"kafka error: {msg.error()}")
        return self._handle_message(msg)

    def run(self, max_messages: Optional[int] = None) -> int:
        """Consume until `max_messages` handled (engine mode). Returns handled count."""
        handled = 0
        while max_messages is None or handled < max_messages:
            if self.poll_once() is None:
                continue
            handled += 1
        return handled

    def close(self) -> None:
        self._consumer.close()