"""Phase 2 — Kafka integration tests against a real broker.

Requires Docker + Compose services (see phase 0 fixture). Proves the full
produce -> consume -> dedupe -> commit -> lag loop with zero duplicates.

Robustness: topics accumulate messages across tests within a module run, so a
fresh consumer group (offset=earliest) re-reads older events. Every assertion
therefore filters its OWN payload by the unique `event_id` — the poll result
list (`delivered`) records every message read off the wire, while the handler's
inner list records what was actually applied. Comparing the two isolates each
test from leftovers.
"""

import time
from pathlib import Path
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent

pytestmark = pytest.mark.integration

BOOTSTRAP = "localhost:9092"
GROUP = "integration-test-group"


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
    import subprocess
    import json as _json

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


def _consume_until(handler, marker_id: str, topics: list[str], max_polls: int = 1200) -> list[dict]:
    """Consume with a fresh group (earliest) until the marker event is delivered.

    Returns every payload read off the wire (including duplicates), i.e. what
    `poll_once` returned, up to and including the marker.
    """
    from streaming.consumer import EngineConsumer

    delivered: list[dict] = []
    consumer = EngineConsumer(BOOTSTRAP, f"it-{uuid4().hex[:8]}", topics, handler)
    try:
        for _ in range(max_polls):
            if any(p.get("event_id") == marker_id for p in delivered):
                break
            payload = consumer.poll_once(timeout=0.2)
            if payload is not None:
                delivered.append(payload)
    finally:
        consumer.close()
    assert any(p.get("event_id") == marker_id for p in delivered), "end-marker event never delivered"
    return delivered


def _produce(events: list[dict], topic: str) -> None:
    from streaming.producer import EventProducer

    producer = EventProducer(BOOTSTRAP, topic)
    for ev in events:
        producer.send(ev)
    remaining = producer.flush(timeout=15)
    assert remaining == 0, f"producer flush left {remaining} messages undelivered"


def _now():
    from datetime import datetime, timezone

    return datetime(2026, 6, 1, 10, 0, tzinfo=timezone.utc)


def _build_event(*, event_type, user_id, entity_id, ts, bytes_moved=0, geo=None):
    from streaming.producer import normalize_payload
    from simulator.schema import build_event

    ev = build_event(
        entity_type="user", entity_id=entity_id, user_id=user_id, event_type=event_type,
        actor=user_id, source_entity=f"LPT-{entity_id[-2:]}", target_entity=f"LPT-{entity_id[-2:]}", ts=ts,
        bytes_moved=bytes_moved, geo=geo or {"city": "Chennai", "lat": 13.08, "lon": 80.27},
    )
    return normalize_payload(ev)


@REQUIRE_DOCKER
class TestProduceConsume:
    def test_roundtrip_auth_events(self, kafka_up):
        ev = _build_event(event_type="login", user_id="EMP001", entity_id="EMP001", ts=_now())
        marker = _build_event(event_type="failure", user_id="EMP001", entity_id="EMP001", ts=_now())
        _produce([ev, marker], topic="auth-events")

        seen: list[dict] = []
        delivered = _consume_until(seen.append, marker["event_id"], ["auth-events"])
        ours = [p for p in seen if p["event_id"] == ev["event_id"]]
        assert ours, "target login event was not consumed"
        assert ours[0]["event_type"] == "login"
        assert ours[0]["user_id"] == "EMP001"
        # the marker proves we read past our payload (tail position)
        assert delivered[-1]["event_type"] == "failure"

    def test_redelivery_at_least_once(self, kafka_up):
        # Produce the SAME event twice, then an end-marker. At-least-once
        # delivery means the consumer reads BOTH copies off the wire.
        ev = _build_event(event_type="mfa", user_id="EMP007", entity_id="EMP007", ts=_now())
        marker = _build_event(event_type="failure", user_id="EMP007", entity_id="EMP007", ts=_now())
        _produce([ev, ev, marker], topic="auth-events")

        seen: list[dict] = []
        delivered = _consume_until(seen.append, marker["event_id"], ["auth-events"])
        on_the_wire = [p for p in delivered if p["event_id"] == ev["event_id"]]
        assert len(on_the_wire) == 2, "expected both duplicates delivered at-least-once"
        assert on_the_wire[0]["event_id"] == on_the_wire[1]["event_id"] == ev["event_id"]

    def test_duplicate_event_ids_rejected(self, kafka_up):
        # Same redelivery behind an IdempotentHandler: both copies arrive on
        # the wire, but only ONE is forwarded to the handler (dedupe).
        from streaming.dedupe import IdempotentHandler

        ev = _build_event(event_type="login", user_id="EMP011", entity_id="EMP011", ts=_now())
        marker = _build_event(event_type="failure", user_id="EMP011", entity_id="EMP011", ts=_now())
        _produce([ev, ev, marker], topic="auth-events")

        inner: list[dict] = []
        idem = IdempotentHandler(inner.append)
        delivered = _consume_until(idem, marker["event_id"], ["auth-events"])
        on_the_wire = [p for p in delivered if p["event_id"] == ev["event_id"]]
        applied = [p for p in inner if p["event_id"] == ev["event_id"]]
        assert len(on_the_wire) == 2, "both copies delivered"
        assert len(applied) == 1, f"duplicate event_id applied {len(applied)} times"

    def test_per_entity_ordering_preserved(self, kafka_up):
        # Same partition key => same partition => Kafka preserves arrival order.
        first = _build_event(event_type="login", user_id="EMP202", entity_id="EMP202", ts=_now())
        second = _build_event(event_type="login", user_id="EMP202", entity_id="EMP202", ts=_now())
        marker = _build_event(event_type="failure", user_id="EMP202", entity_id="EMP202", ts=_now())
        _produce([first, second, marker], topic="auth-events")

        seen: list[dict] = []
        _consume_until(seen.append, marker["event_id"], ["auth-events"])
        ours = [p for p in seen if p["event_id"] in (first["event_id"], second["event_id"])]
        assert [p["event_id"] for p in ours] == [first["event_id"], second["event_id"]]

    def test_multiple_topics_routed(self, kafka_up):
        ev = _build_event(
            event_type="download", user_id="EMPDL", entity_id="EMPDL", ts=_now(), bytes_moved=2048,
            geo={"city": "Mumbai", "lat": 19.08, "lon": 72.88},
        )
        marker = _build_event(event_type="upload", user_id="EMPDL", entity_id="EMPDL", ts=_now())
        _produce([ev, marker], topic="file-events")

        seen: list[dict] = []
        delivered = _consume_until(seen.append, marker["event_id"], ["file-events"])
        ours = [p for p in seen if p["event_id"] == ev["event_id"]]
        assert ours and ours[0]["event_type"] == "download"
        assert delivered[-1]["event_type"] == "upload"


@REQUIRE_DOCKER
class TestAdmin:
    def test_ensure_topics_respects_partition_counts(self, kafka_up):
        from streaming.admin import ensure_topics
        from streaming.topics import TOPICS
        from streaming.monitor import health

        ensure_topics(BOOTSTRAP)
        for name, partitions in TOPICS.items():
            t = health(BOOTSTRAP, topics=[name])["topics"][name]
            assert t["exists"] is True
            assert t["partitions"] == partitions, f"{name} should have {partitions}"

    def test_ensure_topics_is_idempotent(self, kafka_up):
        from streaming.admin import ensure_topics

        first = ensure_topics(BOOTSTRAP)
        second = ensure_topics(BOOTSTRAP)
        assert first == second
        assert all("ERROR" not in v for v in second.values())
        assert all(v in ("created", "exists") for v in second.values())

    def test_ensure_topics_creates_missing(self, kafka_up):
        import time
        from confluent_kafka.admin import AdminClient
        from streaming.admin import ensure_topics
        from streaming.monitor import health

        ac = AdminClient({"bootstrap.servers": BOOTSTRAP})
        fs = ac.delete_topics(["privilege-events"], operation_timeout=30)
        for _topic, fut in fs.items():
            fut.result(timeout=30)

        # topic deletion is async — wait until the broker metadata no longer lists it
        deadline = time.time() + 30
        while time.time() < deadline:
            if ac.list_topics(timeout=10).topics.get("privilege-events") is None:
                break
            time.sleep(1)
        else:
            pytest.fail("privilege-events was not deleted within 30s")

        result = ensure_topics(BOOTSTRAP)
        assert result["privilege-events"] == "created"
        # second call must now report it as already existing
        assert ensure_topics(BOOTSTRAP)["privilege-events"] == "exists"

        # and the broker metadata agrees it is back with the right partitions
        h = health(BOOTSTRAP, topics=["privilege-events"])["topics"]["privilege-events"]
        assert h["exists"] is True
        assert h["partitions"] == 2


@REQUIRE_DOCKER
class TestMonitor:
    def test_health_reports_topics(self, kafka_up):
        from streaming.monitor import health

        h = health(BOOTSTRAP, topics=["auth-events", "file-events", "privilege-events"])
        assert h["kafka"] is True
        assert h["topics"]["auth-events"]["exists"] is True
        assert h["topics"]["auth-events"]["partitions"] == 4
        assert h["topics"]["privilege-events"]["exists"] is True
        assert h["topics"]["privilege-events"]["partitions"] == 2

    def test_health_graceful_when_broker_down(self):
        from streaming.monitor import health

        h = health("localhost:19092", topics=["auth-events"])
        assert h["kafka"] is False

    def test_consumer_lag_computes(self, kafka_up):
        from streaming.monitor import consumer_lag

        lag = consumer_lag(BOOTSTRAP, GROUP, ["auth-events"])
        assert "error" not in lag
        assert len(lag) == 4  # auth-events has 4 partitions
        assert all(isinstance(v, int) for v in lag.values())

    def test_consumer_lag_reports_real_backlog(self, kafka_up):
        # Produce an event never consumed; lag for a fresh group that has
        # consumed nothing must be > 0 on the target partition.
        from streaming.monitor import consumer_lag

        ev = _build_event(event_type="login", user_id="LAGPR", entity_id="LAGPR", ts=_now())
        _produce([ev], topic="auth-events")
        lag = consumer_lag(BOOTSTRAP, f"never-consumed-{uuid4().hex[:4]}", ["auth-events"])
        assert "error" not in lag
        assert sum(v for v in lag.values() if isinstance(v, int)) > 0