"""Phase 3 — PostgreSQL storage integration tests against a real Postgres + Kafka.

Proves the BUILD_METHODOLOGY gate for Phase 3:
  - every table exists per schema (after Alembic head)
  - seed org loads (idempotent)
  - insert -> read roundtrip
  - dedupe `ON CONFLICT (event_id) DO NOTHING`
  - DAO functions pass
  - end-to-end: simulator -> producer -> Kafka consumer -> raw_events (dupes absorbed)
"""

import subprocess
import sys
import time
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent

pytestmark = pytest.mark.integration

BOOTSTRAP = "localhost:9092"
DSN = "postgresql://ueba:ueba_secret@localhost:5432/ueba"


def _docker_available() -> bool:
    try:
        subprocess.run(["docker", "info"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=15)
        return True
    except Exception:
        return False


REQUIRE_DOCKER = pytest.mark.skipif(
    not _docker_available(),
    reason="Docker daemon not reachable — Postgres integration tests need Docker",
)


def _compose(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", "compose", *args], cwd=ROOT, capture_output=True, text=True, timeout=240)


def _healthy(service: str) -> bool:
    out = _compose(["ps", "--format", "json"]).stdout
    for line in out.splitlines():
        try:
            info = json.loads(line)
            if info.get("Service") == service:
                return info.get("Health") == "healthy"
        except json.JSONDecodeError:
            continue
    return False


@pytest.fixture(scope="module")
def pg():
    if not _docker_available():
        pytest.skip("Docker daemon not reachable — Postgres integration tests need Docker")

    _compose(["up", "-d", "postgres", "kafka"])
    deadline = time.time() + 180
    while time.time() < deadline and not (_healthy("postgres") and _healthy("kafka")):
        time.sleep(5)

    # apply schema migrations to the latest head
    proc = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "db/alembic.ini", "upgrade", "head"],
        cwd=ROOT, capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, f"alembic upgrade failed:\n{proc.stderr}"

    # provision the streaming topics (fresh broker has none after compose recycle)
    from streaming.admin import ensure_topics

    ensure_topics(BOOTSTRAP)

    from db.conn import connect

    conn = connect(DSN)
    yield conn
    conn.close()
    _compose(["down"])


@pytest.fixture(autouse=True)
def _clean_db(pg):
    pg.rollback()
    with pg.cursor() as cur:
        cur.execute(
            "TRUNCATE users, peer_groups, entities, raw_events, behavioral_profiles,"
            " feature_windows, alerts, incidents, users_accounts, analyst_actions, ground_truth"
            " RESTART IDENTITY CASCADE"
        )
    pg.commit()
    yield


def _event_dict(*, event_type, user_id="EMP001", entity_id="EMP001", **over):
    from datetime import datetime, timezone
    from simulator.schema import build_event
    from streaming.producer import normalize_payload

    ev = build_event(
        entity_type="user", entity_id=entity_id, user_id=user_id, event_type=event_type,
        actor=user_id, source_entity=f"LPT-{entity_id[-2:]}", target_entity="share",
        ts=datetime.now(timezone.utc), **over,
    )
    return normalize_payload(ev)


@REQUIRE_DOCKER
class TestSchema:
    def test_all_llc_tables_exist(self, pg):
        from db.dao import schema_tables, SCHEMA_TABLES

        present = schema_tables(pg)
        assert set(SCHEMA_TABLES) <= present, set(SCHEMA_TABLES) - present

    def test_every_table_has_required_columns(self, pg):
        expected = {
            "raw_events": {"event_id", "ts", "event_type", "actor", "source_entity", "target_entity", "peer_entity"},
            "users": {"emp_id", "name", "department", "peer_group_id", "sensitivity_tier"},
            "entities": {"entity_id", "kind", "owner_user_id"},
            "peer_groups": {"name", "baseline_features"},
            "behavioral_profiles": {"entity_ref", "level", "confidence", "feature_stats"},
            "feature_windows": {"entity_ref", "window_start", "vector"},
            "alerts": {"entity_ref", "severity", "risk", "status", "evidence_refs"},
            "incidents": {"entity_ref", "severity", "risk", "status", "entity_chain", "evidence_refs"},
            "users_accounts": {"username", "password_hash", "role", "disabled"},
            "analyst_actions": {"incident_id", "action", "actor_user", "impact"},
            "ground_truth": {"scenario", "entity_id", "rule", "expected_risk_band"},
        }
        with pg.cursor() as cur:
            for table, columns in expected.items():
                cur.execute(
                    "SELECT column_name FROM information_schema.columns WHERE table_name = %s",
                    (table,),
                )
                names = {row[0] for row in cur.fetchall()}
                missing = columns - names
                assert not missing, f"{table} missing columns: {missing}"

    def test_raw_events_has_unique_event_id_full_constraint(self, pg):
        with pg.cursor() as cur:
            cur.execute(
                """
                SELECT count(*) FROM pg_constraint c
                JOIN pg_class t ON t.oid = c.conrelid
                WHERE t.relname = 'raw_events' AND c.contype = 'p'
                """
            )
            assert cur.fetchone()[0] == 1, "raw_events lacks a PRIMARY KEY (dedupe key)"


@REQUIRE_DOCKER
class TestSeed:
    def test_seed_org_loads_correct_counts(self, pg):
        from simulator.org import generate_org
        from db.seed import seed_org, seed_accounts

        counts = seed_org(pg, generate_org(seed=42))
        assert counts["peer_groups"] == 7
        assert counts["users"] == 100
        assert counts["entities"] == 80  # 50 devices + 20 servers + 10 apps

        accounts = seed_accounts(pg)
        assert accounts == 2

    def test_seed_is_idempotent(self, pg):
        from simulator.org import generate_org
        from db.seed import seed_org, seed_accounts

        org = generate_org(seed=42)
        seed_org(pg, org)
        again = seed_org(pg, org)
        assert again["users"] == 0  # nothing new inserted on re-run
        seed_accounts(pg)
        assert seed_accounts(pg) == 0

    def test_seeded_users_have_peer_groups_and_devices(self, pg):
        from simulator.org import generate_org
        from db.seed import seed_org
        from db.conn import dict_cursor

        seed_org(pg, generate_org(seed=42))
        with dict_cursor(pg) as cur:
            cur.execute(
                "SELECT count(*) AS n FROM users u JOIN peer_groups g ON g.id = u.peer_group_id"
            )
            assert cur.fetchone()["n"] == 100
            cur.execute(
                "SELECT count(*) AS n FROM entities WHERE kind = 'device' AND owner_user_id IS NOT NULL"
            )
            assert cur.fetchone()["n"] == 50

    def test_demo_accounts_verify(self, pg):
        from db.seed import seed_accounts
        from db.conn import dict_cursor
        from db.passwords import verify_password

        seed_accounts(pg, {"analyst": "analyst", "admin": "admin"})
        with dict_cursor(pg) as cur:
            cur.execute("SELECT username, password_hash, role FROM users_accounts ORDER BY username")
            rows = {r["username"]: r for r in cur.fetchall()}
        assert set(rows) == {"admin", "analyst"}
        assert rows["analyst"]["role"] == "analyst"
        assert rows["admin"]["role"] == "admin"
        assert verify_password("analyst", rows["analyst"]["password_hash"])
        assert verify_password("admin", rows["admin"]["password_hash"])


@REQUIRE_DOCKER
class TestDao:
    def test_insert_read_roundtrip(self, pg):
        from db.dao import insert_event, get_event

        payload = _event_dict(
            event_type="login", bytes_moved=0, geo={"city": "Chennai", "lat": 13.08, "lon": 80.27}
        )
        assert insert_event(pg, payload) is True
        got = get_event(pg, payload["event_id"])
        assert got is not None
        assert got["event_type"] == "login"
        assert got["actor"] == "EMP001"
        assert got["geo"]["city"] == "Chennai"

    def test_dedupe_on_conflict_rejects_duplicate_event_id(self, pg):
        from db.dao import insert_event, get_event, count_events

        payload = _event_dict(event_type="mfa")
        assert insert_event(pg, payload) is True
        assert insert_event(pg, payload) is False  # redelivery rejected by DB
        assert count_events(pg) == 1
        assert get_event(pg, payload["event_id"]) is not None

    def test_dao_read_helpers(self, pg):
        from db.dao import insert_event, count_events, list_recent_events, events_for_user

        for user, et in (("EMP001", "login"), ("EMP002", "download"), ("EMP002", "upload")):
            insert_event(pg, _event_dict(event_type=et, user_id=user, entity_id=user))

        assert count_events(pg) == 3
        recent = list_recent_events(pg, limit=5)
        assert len(recent) == 3
        assert all("event_type" in r for r in recent)
        emp2 = events_for_user(pg, "EMP002")
        assert sorted(e["event_type"] for e in emp2) == ["download", "upload"]
        assert events_for_user(pg, "NOPE") == []

    def test_insert_requires_valid_event_type_constraint(self, pg):
        from db.conn import dict_cursor

        # DB-level enum guard: invalid event_type must be rejected by the CHECK
        from datetime import datetime, timezone
        with pytest.raises(Exception):
            with pg.cursor() as cur:
                cur.execute(
                    "INSERT INTO raw_events (event_id, ts, ingested_at, event_type, outcome, sensitivity)"
                    " VALUES (%s, now(), now(), %s, 'success', 'internal')",
                    ("bad-type-evt", "teleport"),
                )
            pg.commit()
        pg.rollback()  # aborted transaction must be rolled back before reuse


@REQUIRE_DOCKER
class TestConsumerPersistence:
    def _consume_fresh_after_produce(self, handler, make_batch, min_delivered=1):
        from streaming.consumer import EngineConsumer
        from uuid import uuid4

        delivered: list = []
        consumer = EngineConsumer(BOOTSTRAP, f"db-it-{uuid4().hex[:8]}", ["auth-events"], handler, auto_offset_reset="latest")
        try:
            joined = False
            for _ in range(30):
                consumer.poll_once(timeout=0.5)
                if consumer._consumer.assignment():
                    joined = True
                    break
            assert joined, "consumer never joined group"
            marker_id = make_batch()
            marker_seen = False
            for _ in range(300):
                if marker_seen and len(delivered) >= min_delivered:
                    break
                payload = consumer.poll_once(timeout=0.2)
                if payload is not None:
                    delivered.append(payload)
                    if payload.get("event_id") == marker_id:
                        marker_seen = True
            assert marker_seen, "marker never delivered"
            assert len(delivered) >= min_delivered, f"expected at least {min_delivered} deliveries, got {len(delivered)}"
        finally:
            consumer.close()
        return delivered

    def test_events_flow_kafka_to_raw_events(self, pg):
        from db.persist import build_persist_handler
        from db.dao import get_event, count_events

        ev = _event_dict(event_type="login", user_id="KT000", entity_id="KT000")
        marker = _event_dict(event_type="failure", user_id="KT000", entity_id="KT000")
        results: list = []

        handler = build_persist_handler()
        wrapped = lambda payload: results.append(handler(payload))  # noqa: E731

        def make_batch():
            from streaming.producer import EventProducer
            pro = EventProducer(BOOTSTRAP, "auth-events")
            pro.send(ev)
            pro.send(marker)
            pro.flush(timeout=15)
            return marker["event_id"]

        self._consume_fresh_after_produce(wrapped, make_batch)
        assert get_event(pg, ev["event_id"]) is not None, "event not persisted to raw_events"
        assert get_event(pg, ev["event_id"])["event_type"] == "login"
        assert count_events(pg) == 2  # login + marker

    def test_redelivered_duplicate_absorbed_by_db(self, pg):
        from db.persist import build_persist_handler
        from db.dao import count_events, get_event

        ev = _event_dict(event_type="mfa", user_id="KT111", entity_id="KT111")
        results: list = []
        handler = build_persist_handler()
        wrapped = lambda payload: results.append(handler(payload))  # noqa: E731

        def make_batch():
            from streaming.producer import EventProducer
            pro = EventProducer(BOOTSTRAP, "auth-events")
            pro.send(ev)
            pro.send(dict(ev))  # redelivered copy
            pro.flush(timeout=15)
            return ev["event_id"]

        self._consume_fresh_after_produce(wrapped, make_batch, min_delivered=2)
        # both copies reached the handler, but the DB accepted exactly one
        assert results and set(results) == {True, False}, results
        assert count_events(pg) == 1
        assert get_event(pg, ev["event_id"]) is not None