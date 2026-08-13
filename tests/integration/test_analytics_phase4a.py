"""Phase 4A — Behavioral Baselines integration tests (real Postgres).

Proves the Phase 4A gate against a live DB:
  - migration 0002 applies (unique feature_windows upsert key)
  - window upsert/get roundtrip + idempotency
  - profile upsert/get roundtrip (individual/peer_group/global)
  - end-to-end: backfill events -> accumulate windows -> baselines -> cold start

Skipped with an explicit reason when Docker is unavailable.
"""

import subprocess
import sys
import time
import json
from pathlib import Path
from datetime import datetime, timezone, timedelta

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent

pytestmark = pytest.mark.integration

DSN = "postgresql://ueba:ueba_secret@localhost:5432/ueba"


def _docker_available() -> bool:
    try:
        subprocess.run(["docker", "info"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=15)
        return True
    except Exception:
        return False


REQUIRE_DOCKER = pytest.mark.skipif(
    not _docker_available(),
    reason="Docker daemon not reachable — Phase 4A integration tests need Docker",
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
        pytest.skip("Docker daemon not reachable — Phase 4A integration tests need Docker")

    _compose(["up", "-d", "postgres", "kafka"])
    deadline = time.time() + 180
    while time.time() < deadline and not (_healthy("postgres") and _healthy("kafka")):
        time.sleep(5)

    proc = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "db/alembic.ini", "upgrade", "head"],
        cwd=ROOT, capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, f"alembic upgrade failed:\n{proc.stderr}"

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


@REQUIRE_DOCKER
class TestMigration:
    def test_0002_unique_window_constraint_applied(self, pg):
        with pg.cursor() as cur:
            cur.execute(
                """
                SELECT count(*) FROM pg_constraint c
                JOIN pg_class t ON t.oid = c.conrelid
                WHERE t.relname = 'feature_windows' AND c.conname = 'uq_feature_windows_entity_window'
                """
            )
            assert cur.fetchone()[0] == 1, "missing unique constraint on feature_windows(entity_ref, window_start)"


@REQUIRE_DOCKER
class TestWindowDao:
    def _vector(self, volume: int = 1000) -> dict:
        return {
            "entity_ref": "EMP001",
            "window_start": datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc).isoformat(),
            "volume": volume,
            "event_count": 5,
            "active_hours_frac": 0.5,
            "unique_peers": ["SRV-01"],
            "new_peer_count": 0,
            "location_count": 1,
            "location_dist_km": 0.0,
            "dept_distinct": ["HR"],
            "sensitivity_hist": {"internal": 5},
            "fail_rate": 0.0,
            "staleness_days": 0,
        }

    def test_upsert_get_roundtrip(self, pg):
        from db.dao import upsert_window, get_windows

        v = self._vector()
        upsert_window(pg, "EMP001", datetime.fromisoformat(v["window_start"]), v)
        rows = get_windows(pg, "EMP001")
        assert len(rows) == 1
        assert rows[0]["entity_ref"] == "EMP001"
        assert rows[0]["vector"]["volume"] == 1000

    def test_upsert_is_idempotent_same_bucket(self, pg):
        from db.dao import upsert_window, get_windows

        v = self._vector(volume=1000)
        upsert_window(pg, "EMP001", datetime.fromisoformat(v["window_start"]), v)
        upsert_window(pg, "EMP001", datetime.fromisoformat(v["window_start"]), self._vector(volume=9999))
        rows = get_windows(pg, "EMP001")
        assert len(rows) == 1, "same bucket must not duplicate"
        assert rows[0]["vector"]["volume"] == 9999, "upsert should overwrite the bucket"

    def test_get_windows_since_filter(self, pg):
        from db.dao import upsert_window, get_windows

        for day in (1, 2, 3):
            ts = datetime(2026, 1, day, 9, 0, tzinfo=timezone.utc)
            v = dict(self._vector(volume=day), window_start=ts.isoformat())
            upsert_window(pg, "EMP001", ts, v)
        since = datetime(2026, 1, 2, 0, 0, tzinfo=timezone.utc)
        rows = get_windows(pg, "EMP001", since=since)
        assert len(rows) == 2
        assert all(r["window_start"] >= since for r in rows)


@REQUIRE_DOCKER
class TestProfileDao:
    def _row(self, entity_ref: str, level: str) -> dict:
        return {
            "entity_ref": entity_ref,
            "level": level,
            "feature_stats": {"volume": {"mean": 2000.0, "std": 100.0, "count": 20, "confidence": "MED"}},
            "allowed_sets": {"locations": [], "peers": ["SRV-01"], "dept_paths": ["HR"], "sensitivity": ["internal"]},
            "active_window": {"start_hour": 9, "end_hour": 18},
            "confidence": "MED",
            "updated_to": datetime.now(timezone.utc).isoformat(),
        }

    def test_upsert_get_roundtrip(self, pg):
        from db.dao import upsert_profile, get_profile

        upsert_profile(pg, self._row("EMP001", "individual"))
        row = get_profile(pg, "EMP001", "individual")
        assert row is not None
        assert row["confidence"] == "MED"
        assert row["feature_stats"]["volume"]["mean"] == 2000.0

    def test_upsert_is_idempotent_same_entity_level(self, pg):
        from db.dao import upsert_profile, get_profile
        from db.conn import dict_cursor

        upsert_profile(pg, self._row("EMP001", "individual"))
        row = dict(self._row("EMP001", "individual"), confidence="HIGH")
        upsert_profile(pg, row)
        got = get_profile(pg, "EMP001", "individual")
        assert got["confidence"] == "HIGH"
        with dict_cursor(pg) as cur:
            cur.execute("SELECT count(*) AS n FROM behavioral_profiles WHERE entity_ref='EMP001' AND level='individual'")
            assert cur.fetchone()["n"] == 1


@REQUIRE_DOCKER
class TestEndToEnd:
    def test_backfill_to_baselines_to_cold_start(self, pg):
        from simulator.org import generate_org
        from simulator.engine import run_backfill
        from analytics.processor import validate
        from analytics.features import accumulate_all, finalize
        from analytics.baseline import build_individual, build_peer_group, build_global, select_level
        from streaming.producer import normalize_payload
        from db.dao import upsert_window, get_windows, upsert_profile, get_profile
        from collections import defaultdict

        org = generate_org(seed=7)
        events = run_backfill(org, days=10, events_per_day=6, seed=7)
        norm = [n for n in (validate(normalize_payload(e)) for e in events) if n is not None]

        windows = accumulate_all(norm)
        by_entity: dict[str, list] = defaultdict(list)
        for w in windows.values():
            vec = finalize(w)
            by_entity[w.entity_ref].append(vec)
            upsert_window(pg, w.entity_ref, w.window_start, vec)

        # every stored window round-trips
        stored = get_windows(pg, list(by_entity.keys())[0])
        assert len(stored) >= 1

        individuals = [build_individual(ref, vecs) for ref, vecs in by_entity.items()]
        peer = build_peer_group("HR", individuals[:20])
        glob = build_global(individuals)

        upsert_profile(pg, peer)
        upsert_profile(pg, glob)
        assert get_profile(pg, "HR", "peer_group") is not None
        assert get_profile(pg, "__global__", "global") is not None

        # cold-start demo: sparse vs rich entity
        first = list(by_entity.keys())[0]
        sparse = build_individual("EMP999", by_entity[first][:3])
        level_sparse, _ = select_level("EMP999", {"individual": sparse, "peer_group": peer, "global": glob})
        rich = build_individual(first, by_entity[first])
        level_rich, _ = select_level(first, {"individual": rich, "peer_group": peer, "global": glob})
        assert level_sparse == "peer_group"
        assert level_rich == "individual"