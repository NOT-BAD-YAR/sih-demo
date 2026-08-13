"""Phase 1 — Common Event Schema tests."""

import pytest
from datetime import datetime, timezone

from simulator.schema import (
    Event,
    build_event,
    from_dict,
    is_valid,
    validate,
    EventValidationError,
    EVENT_TYPES,
    ENTITY_TYPES,
    OUTCOMES,
    SENSITIVITIES,
)

NOW = datetime(2026, 1, 1, 9, 30, tzinfo=timezone.utc)

pytestmark = pytest.mark.unit


def _valid() -> Event:
    return build_event(
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
        bytes_moved=1024,
    )


class TestSchemaEnums:
    def test_entity_types(self):
        assert "user" in ENTITY_TYPES
        assert {"device", "server", "app"} <= set(ENTITY_TYPES)

    def test_event_types_cover_all_canonical_cases(self):
        for et in ("login", "logout", "file_access", "download", "upload", "network_conn", "privilege", "failure"):
            assert et in EVENT_TYPES

    def test_outcomes_and_sensitivities(self):
        assert {"success", "failure"} <= set(OUTCOMES)
        assert {"public", "internal", "confidential", "restricted"} <= set(SENSITIVITIES)


class TestValidation:
    def test_valid_event_passes(self):
        event = _valid()
        assert validate(event) == []
        assert is_valid(event)

    def test_missing_required_fields_reported(self):
        event = _valid()
        event.entity_id = ""
        problems = validate(event)
        assert any("entity_id" in p for p in problems)

    def test_bad_entity_type_rejected(self):
        event = _valid()
        event.entity_type = "robot"
        assert not is_valid(event)

    def test_bad_event_type_rejected(self):
        event = _valid()
        event.event_type = "explode"
        assert not is_valid(event)

    def test_bad_outcome_rejected(self):
        event = _valid()
        event.outcome = "maybe"
        assert not is_valid(event)

    def test_bad_sensitivity_rejected(self):
        event = _valid()
        event.sensitivity = "topsecret"
        assert not is_valid(event)

    def test_negative_bytes_rejected(self):
        event = _valid()
        event.bytes_moved = -5
        assert not is_valid(event)

    def test_actor_required(self):
        event = _valid()
        event.actor = ""
        assert not is_valid(event)

    def test_geo_must_be_dict(self):
        event = _valid()
        event.geo = "Chennai"
        assert not is_valid(event)


class TestSerialization:
    def test_to_dict_roundtrip(self):
        event = _valid()
        data = event.to_dict
        assert isinstance(data["ts"], str)
        assert data["event_id"] == event.event_id
        restored = from_dict(data)
        assert restored == event

    def test_to_json_parseable(self):
        import json

        payload = json.loads(_valid().to_json)
        assert payload["event_type"] == "login"

    def test_from_dict_missing_field_raises(self):
        with pytest.raises(EventValidationError):
            from_dict({"entity_type": "user"})

    def test_chain_includes_nonempty_graph_keys(self):
        event = _valid()
        assert event.actor in event.chain
        assert event.source_entity in event.chain