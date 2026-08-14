"""Phase 8 — agent integration: normalized agent events → real Kafka → consumable.

Proves the agent's batch path ships schema-valid Common Event Schema events on
the correct topics through the same producer path the engine consumes. Requires
Docker + Kafka (same gate as Phase 2's integration suite).
"""

import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent

pytestmark = pytest.mark.integration

BOOTSTRAP = "localhost:9092"


def _docker_available() -> bool:
    import subprocess

    try:
        subprocess.run(["docker", "info"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=15)
        return True
    except Exception:
        return False


REQUIRE_DOCKER = pytest.mark.skipif(
    not _docker_available(),
    reason="Docker daemon not reachable — Kafka integration tests need Docker",
)


@pytest.fixture(scope="module")
def kafka_up():
    if not _docker_available():
        pytest.skip("Docker daemon not reachable — Kafka integration tests need Docker")
    import json as _json
    import subprocess

    def _compose(args):
        return subprocess.run(["docker", "compose", *args], cwd=ROOT, capture_output=True, text=True, timeout=180)

    _compose(["up", "-d"])
    deadline = time.time() + 180
    kafka_healthy = False
    while time.time() < deadline:
        out = _compose(["ps", "--format", "json"]).stdout
        try:
            for line in out.splitlines():
                info = _json.loads(line)
                if info.get("Service") == "kafka" and info.get("Health") == "healthy":
                    kafka_healthy = True
                    break
        except _json.JSONDecodeError:
            pass
        if kafka_healthy:
            break
        time.sleep(5)
    if not kafka_healthy:
        pytest.skip("Kafka did not reach healthy within 180s")
    from streaming.admin import ensure_topics

    ensure_topics(BOOTSTRAP)
    yield
    _compose(["down"])


def _agent_events(marker_id: str) -> list[dict]:
    """Normalized agent wire payloads (one per reader source) plus a marker."""
    from agents.windows_agent.normalize import to_schema

    host = "DESKTOP-INT"
    raw_records = [
        dict(source="security_log", event_id_win=4624, ts="2026-08-14T05:00:00Z", user="CORP\\alice", ip="10.0.0.5"),
        dict(source="sysmon", event_id_win=11, ts="2026-08-14T05:00:01Z", user="CORP\\alice",
             target_filename="C:\\Users\\alice\\report.xlsx", file_size=2048),
        dict(source="file_watcher", ts="2026-08-14T05:00:02Z", path="C:\\Users\\alice\\archive.zip", size=4096),
        dict(source="usb", ts="2026-08-14T05:00:03Z", device="SanDisk", device_id="USBSTOR\\DISK&VEN_X", action="inserted"),
        dict(source="process", ts="2026-08-14T05:00:04Z", process="powershell.exe", pid=4242, action="started"),
    ]
    events = []
    for raw in raw_records:
        ev = to_schema(raw, host)
        assert ev is not None, raw
        events.append(ev.to_dict)
    marker = to_schema(
        dict(source="security_log", event_id_win=4624, ts="2026-08-14T05:00:05Z", user=f"CORP\\{marker_id}", ip="10.9.9.9"),
        host,
    )
    marker_dict = marker.to_dict
    marker_dict["event_id"] = marker_id
    events.append(marker_dict)
    return events


def _ship_via_agent_sink(events: list[dict], bootstrap: str) -> None:
    """Push normalized events through the agent's own KafkaSink + BatchBuffer."""
    from agents.windows_agent.batch import run_batch
    from agents.windows_agent.main import KafkaSink

    sink = KafkaSink(bootstrap)
    try:
        run_batch(events, sink, max_size=3)
    finally:
        sink.close()


def _consume_until(marker_id: str, topic: str, max_polls: int = 1200) -> list[dict]:
    from streaming.consumer import EngineConsumer

    seen: list[dict] = []
    consumer = EngineConsumer(BOOTSTRAP, f"it-agent-{uuid4().hex[:8]}", [topic], seen.append)
    try:
        for _ in range(max_polls):
            if any(p.get("event_id") == marker_id for p in seen):
                break
            payload = consumer.poll_once(timeout=0.2)
            if payload is not None:
                seen.append(payload)
    finally:
        consumer.close()
    assert any(p.get("event_id") == marker_id for p in seen), "agent marker never delivered"
    return seen


@REQUIRE_DOCKER
class TestAgentToKafka:
    def test_agent_events_reach_expected_topics(self, kafka_up):
        marker_id = f"AGENT-{uuid4().hex[:8]}"
        events = _agent_events(marker_id)
        _ship_via_agent_sink(events, BOOTSTRAP)

        # login+sysmon→auth/file events; marker rides auth-events
        auth = _consume_until(marker_id, "auth-events")
        payloads = {p["event_id"]: p for p in auth}
        marker = payloads[marker_id]
        assert marker["event_type"] == "login"
        assert marker["user_id"].startswith("CORP") is False  # domain stripped
        assert marker["entity_id"] == "DESKTOP-INT"
        assert marker["source_entity"] == "DESKTOP-INT"

    def test_agent_events_are_schema_valid_on_the_wire(self, kafka_up):
        from simulator.schema import from_dict, is_valid

        marker_id = f"AGENT-{uuid4().hex[:8]}"
        events = _agent_events(marker_id)
        _ship_via_agent_sink(events, BOOTSTRAP)

        auth = _consume_until(marker_id, "auth-events")
        for payload in auth:
            if payload["event_id"].startswith("AGENT-"):
                ev = from_dict(payload)
                assert is_valid(ev), payload

    def test_multiple_agent_sources_routed_to_correct_topics(self, kafka_up):
        from streaming.topics import EVENT_TYPE_TO_TOPIC

        marker_id = f"AGENT-{uuid4().hex[:8]}"
        events = _agent_events(marker_id)
        _ship_via_agent_sink(events, BOOTSTRAP)

        auth = _consume_until(marker_id, "auth-events")
        got_auth = {p["event_id"]: p for p in auth}
        assert got_auth[marker_id]["event_type"] == "login"

        # file_watcher + sysmon(file) + usb + process all ship too
        for event in events:
            topic = EVENT_TYPE_TO_TOPIC[event["event_type"]]
            seen = _consume_until(event["event_id"], topic)
            found = [p for p in seen if p["event_id"] == event["event_id"]]
            assert found, f"agent event {event['event_id']} never reached {topic}"
            assert found[0]["event_type"] == event["event_type"]

    def test_batch_flush_via_agent_pipeline_preserves_all(self, kafka_up):
        from agents.windows_agent.batch import run_batch
        from agents.windows_agent.main import KafkaSink

        marker_id = f"AGENT-{uuid4().hex[:8]}"
        events = _agent_events(marker_id)
        sink = KafkaSink(BOOTSTRAP)
        try:
            sent = run_batch(events, sink, max_size=2)
        finally:
            sink.close()
        assert sent == len(events)

        auth = _consume_until(marker_id, "auth-events")
        assert any(p["event_id"] == marker_id for p in auth)