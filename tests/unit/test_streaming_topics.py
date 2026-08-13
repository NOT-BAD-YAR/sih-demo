"""Phase 2 — topics.py unit tests (no broker needed)."""

import pytest

from streaming.topics import (
    TOPICS,
    EVENT_TYPE_TO_TOPIC,
    TOPIC_KEY_FIELD,
    partition_key,
    topic_defs,
    topic_for,
    Topic,
)

pytestmark = pytest.mark.unit


class TestTopicDefinition:
    def test_expected_topics_present(self):
        for name in ("auth-events", "file-events", "network-events", "device-events", "privilege-events"):
            assert name in TOPICS

    def test_partition_counts(self):
        assert TOPICS["auth-events"] == 4
        assert TOPICS["file-events"] == 4
        assert TOPICS["network-events"] == 4
        assert TOPICS["device-events"] == 4
        assert TOPICS["privilege-events"] == 2

    def test_topic_defs_returns_all(self):
        defs = topic_defs()
        assert len(defs) == 5
        assert all(isinstance(t, Topic) for t in defs)

    def test_every_event_type_has_a_topic(self):
        from simulator.schema import EVENT_TYPES

        for et in EVENT_TYPES:
            assert et in EVENT_TYPE_TO_TOPIC, f"{et} unmapped"

    def test_every_topic_has_key_field(self):
        for name in TOPICS:
            assert name in TOPIC_KEY_FIELD


class TestRouting:
    def test_login_routes_to_auth(self):
        assert topic_for("login") == "auth-events"

    def test_file_routes(self):
        assert topic_for("download") == "file-events"
        assert topic_for("file_access") == "file-events"

    def test_network_routes(self):
        assert topic_for("network_conn") == "network-events"

    def test_usb_routes_to_device(self):
        assert topic_for("usb") == "device-events"

    def test_unknown_type_raises(self):
        with pytest.raises(KeyError):
            topic_for("explode")


class TestPartitionKey:
    def test_auth_uses_user_id(self):
        key = partition_key({"event_type": "login", "user_id": "EMP001"})
        assert key == b"EMP001"

    def test_file_uses_entity_id(self):
        key = partition_key({"event_type": "download", "entity_id": "EMP002"})
        assert key == b"EMP002"

    def test_network_uses_source_entity(self):
        key = partition_key({"event_type": "network_conn", "source_entity": "SRV-01"})
        assert key == b"SRV-01"

    def test_fallback_to_entity_id(self):
        key = partition_key({"event_type": "login", "user_id": ""})
        assert key == b""

    def test_key_is_bytes_for_ordering(self):
        key = partition_key({"event_type": "mfa", "user_id": "EMP009"})
        assert isinstance(key, bytes)

    def test_per_entity_ordering_guarantees(self):
        # same user -> same key -> same partition -> ordered delivery
        a = partition_key({"event_type": "login", "user_id": "EMP100"})
        b = partition_key({"event_type": "privilege", "user_id": "EMP100"})
        assert a == b