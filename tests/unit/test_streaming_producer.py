"""Phase 2 — producer normalize + consumer handler contract (no broker)."""

import json
import pytest
from datetime import datetime, timezone

from streaming.producer import normalize_payload
from streaming.consumer import EngineConsumer
from streaming.dedupe import IdempotentHandler
from simulator.schema import build_event

pytestmark = pytest.mark.unit


class _FakeCommit:
    """Broker stand-in that records synchronous commits."""

    def __init__(self):
        self.commits = 0

    def commit(self, asynchronous=False, offsets=None):
        self.commits += 1


def _event(**over) -> dict:
    defaults = dict(
        event_type="login",
        entity_type="user",
        entity_id="EMP001",
        user_id="EMP001",
        actor="EMP001",
        source_entity="LPT-001",
        target_entity="LPT-001",
        ts=datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc),
        geo={"city": "Chennai", "lat": 13.08, "lon": 80.27},
    )
    defaults.update(over)
    return defaults


def _wire_event(**over) -> dict:
    """Ship-ready dict (JSON-safe) as it arrives on the Kafka wire."""
    base = _event(**over)
    base.setdefault("event_id", "EVT-UNIT-001")
    base["ts"] = datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc).isoformat()
    base["ingested_at"] = datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc).isoformat()
    return base


class TestNormalizePayload:
    def test_event_object_becomes_dict(self):
        ev = build_event(**_event())
        payload = normalize_payload(ev)
        assert isinstance(payload, dict)
        assert payload["event_type"] == "login"
        assert payload["bytes"] == 0  # canonical field name preserved

    def test_ingested_at_stamped(self):
        ev = build_event(**_event())
        payload = normalize_payload(ev)
        assert payload["ingested_at"]  # populated by producer

    def test_json_serializable(self):
        ev = build_event(**_event())
        payload = normalize_payload(ev)
        json.dumps(payload)  # must not raise


class TestConsumerContract:
    def test_handler_called_with_payload(self):
        seen = []

        def handler(payload: dict):
            seen.append(payload)

        event = _wire_event()

        class _FakeMsg:
            def value(self):
                return json.dumps(event).encode()

            def error(self):
                return None

        consumer = EngineConsumer.__new__(EngineConsumer)
        consumer._handler = handler
        consumer._consumer = _FakeCommit()
        payload = consumer._handle_message(_FakeMsg())
        assert seen and seen[0]["event_id"] == event["event_id"]
        assert payload["event_id"] == event["event_id"]

    def test_commit_happens_after_handler_success(self):
        seen = []

        def handler(payload: dict):
            seen.append(payload)

        class _FakeMsg:
            def value(self):
                return json.dumps(_wire_event()).encode()

            def error(self):
                return None

        consumer = EngineConsumer.__new__(EngineConsumer)
        consumer._handler = handler
        consumer._consumer = _FakeCommit()
        consumer._handle_message(_FakeMsg())
        # at-least-once: offset committed exactly once after successful handling
        assert consumer._consumer.commits == 1

    def test_no_commit_when_handler_raises(self):
        def handler(payload: dict):
            raise RuntimeError("persist failed")

        class _FakeMsg:
            def value(self):
                return json.dumps(_wire_event()).encode()

            def error(self):
                return None

        consumer = EngineConsumer.__new__(EngineConsumer)
        consumer._handler = handler
        consumer._consumer = _FakeCommit()
        with pytest.raises(RuntimeError):
            consumer._handle_message(_FakeMsg())
        # handler failed -> offset NOT committed -> message redelivers (at-least-once)
        assert consumer._consumer.commits == 0

    def test_at_least_once_contract_documented(self):
        # The contract: commit happens only after handler returns without raising.
        # Verify the consumer config sets enable.auto.commit=False.
        consumer = EngineConsumer(
            bootstrap="unused",
            group_id="g",
            topics=["auth-events"],
            handler=lambda _p: None,
        )
        try:
            assert consumer._conf["enable.auto.commit"] is False
        finally:
            consumer.close()  # may raise; group not actually created


class TestEngineConsumerRun:
    """run() counts only successfully delivered payloads (None polls ignored)."""

    def test_run_stops_after_max_messages(self):
        consumed = [
            None,
            {"event_id": "E1"},
            {"event_id": "E2"},
            None,
            {"event_id": "E3"},
        ]

        consumer = EngineConsumer.__new__(EngineConsumer)
        consumer.poll_once = lambda _timeout=1.0: consumed.pop(0) if consumed else None
        assert consumer.run(max_messages=2) == 2

    def test_run_returns_zero_when_no_messages(self):
        consumer = EngineConsumer.__new__(EngineConsumer)
        consumer.poll_once = lambda _timeout=1.0: None
        # bounded by max_messages; would loop forever without one
        assert consumer.run(max_messages=0) == 0


class TestIdempotentHandler:
    def test_first_event_processed(self):
        inner = []
        h = IdempotentHandler(inner.append)
        assert h({"event_id": "E1"}) is True
        assert inner == [{"event_id": "E1"}]
        assert h.processed == 1
        assert h.rejected == 0

    def test_duplicate_event_id_rejected(self):
        inner = []
        h = IdempotentHandler(inner.append)
        assert h({"event_id": "X"}) is True
        assert h({"event_id": "X"}) is False
        assert inner == [{"event_id": "X"}]
        assert h.processed == 1
        assert h.rejected == 1

    def test_distinct_events_all_processed(self):
        inner = []
        h = IdempotentHandler(inner.append)
        for i in range(5):
            h({"event_id": f"E{i}"})
        assert len(inner) == 5
        assert h.processed == 5
        assert h.rejected == 0
        assert h.seen_count() == 5

    def test_missing_event_id_never_rejected(self):
        inner = []
        h = IdempotentHandler(inner.append)
        assert h({}) is True
        assert h({}) is True
        assert h.processed == 2
        assert h.rejected == 0