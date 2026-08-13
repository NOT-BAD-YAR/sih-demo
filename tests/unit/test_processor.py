"""Phase 4A — Event Processor (normalize/validate ingress) tests."""

import pytest
from datetime import datetime, timezone, timedelta

from analytics.processor import validate, resolve_user, NormalizedEvent
from simulator.schema import build_event
from streaming.producer import normalize_payload

pytestmark = pytest.mark.unit

NOW = datetime(2026, 1, 15, 10, 30, tzinfo=timezone.utc)


def _payload(**over):
    ev = build_event(
        entity_type="user",
        entity_id="EMP001",
        user_id="EMP001",
        event_type="login",
        actor="EMP001",
        source_entity="LPT-001",
        target_entity="LPT-001",
        ts=NOW,
        ip="10.0.0.1",
        geo={"city": "Chennai", "lat": 13.08, "lon": 80.27},
    )
    payload = normalize_payload(ev)
    payload.update(over)
    return payload


class TestValidate:
    def test_valid_payload_returns_normalized_event(self):
        ev = validate(_payload())
        assert isinstance(ev, NormalizedEvent)
        assert ev.event_type == "login"
        assert ev.entity_id == "EMP001"
        assert ev.bytes_moved == 0

    def test_missing_required_field_returns_none(self):
        payload = _payload()
        del payload["event_id"]
        assert validate(payload) is None

    def test_bad_event_type_returns_none(self):
        assert validate(_payload(event_type="teleport")) is None

    def test_bad_outcome_returns_none(self):
        assert validate(_payload(outcome="maybe")) is None

    def test_future_timestamp_returns_none(self):
        future = datetime.now(timezone.utc) + timedelta(days=1)
        assert validate(_payload(ts=future.isoformat())) is None

    def test_naive_timestamp_returns_none(self):
        assert validate(_payload(ts="2026-01-15T10:30:00")) is None

    def test_negative_bytes_returns_none(self):
        assert validate(_payload(bytes=-5)) is None

    def test_invalid_never_raises(self):
        for bad in ({}, None, [], {"ts": "garbage"}, {"event_id": 1}):
            assert validate(bad) is None

    def test_chain_property(self):
        ev = validate(_payload())
        assert "EMP001" in ev.chain
        assert "LPT-001" in ev.chain


class TestResolveUser:
    def test_user_event_uses_user_id(self):
        ev = validate(_payload())
        assert resolve_user(ev) == "EMP001"

    def test_user_event_without_user_id_falls_back_to_actor(self):
        payload = _payload(user_id="")
        ev = validate(payload)
        assert resolve_user(ev) == "EMP001"

    def test_device_event_resolves_owner_via_mapping(self):
        payload = _payload(entity_type="device", entity_id="LPT-001", user_id="")
        ev = validate(payload)
        assert resolve_user(ev, {"LPT-001": "EMP007"}) == "EMP007"

    def test_device_event_without_mapping_is_gently_empty(self):
        payload = _payload(entity_type="device", entity_id="LPT-999", user_id="")
        ev = validate(payload)
        assert resolve_user(ev, {}) == ""