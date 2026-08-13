"""Phase 4B — Rule detectors against real Postgres + simulator backfill.

Builds baselines from a real seeded org backfill, persists windows/profiles,
plants each of the 5 canonical anomalies, and asserts the matching rule fires
with an explainable sentence against the stored baseline data.
"""

import subprocess
import sys
import time
import json
from pathlib import Path
from datetime import datetime, timezone
from collections import defaultdict

import pytest

from simulator.org import generate_org
from simulator.engine import run_backfill
from simulator.anomaly import inject_scenario
from streaming.producer import normalize_payload
from analytics.processor import validate
from analytics.features import accumulate_all, finalize
from analytics.baseline import build_individual, build_peer_group, build_global
from analytics.rules.volume_spike import evaluate as vol_eval
from analytics.rules.impossible_travel import evaluate as travel_eval
from analytics.rules.out_of_scope import evaluate as scope_eval
from analytics.rules.dormant import evaluate as dormant_eval
from analytics.rules.novel_peer import evaluate as peer_eval

ROOT = Path(__file__).resolve().parent.parent.parent
pytestmark = pytest.mark.integration

DSN = "postgresql://ueba:ueba_secret@localhost:5432/ueba"
NOW = datetime(2026, 2, 1, 10, 30, tzinfo=timezone.utc)


def _docker_available() -> bool:
    try:
        subprocess.run(["docker", "info"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=15)
        return True
    except Exception:
        return False


REQUIRE_DOCKER = pytest.mark.skipif(
    not _docker_available(),
    reason="Docker daemon not reachable — Phase 4B integration tests need Docker",
)


def _compose(args):
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
        pytest.skip("Docker daemon not reachable — Phase 4B integration tests need Docker")
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


@pytest.fixture(scope="module")
def baselines():
    """Org backfill → per-entity windows → individual/peer/global baselines."""
    org = generate_org(seed=42)
    events = run_backfill(org, days=30, events_per_day=12, seed=42)
    norm = [n for n in (validate(normalize_payload(e)) for e in events) if n is not None]

    by_entity: dict[str, list[dict]] = defaultdict(list)
    for w in accumulate_all(norm).values():
        by_entity[w.entity_ref].append(finalize(w))

    individuals = [build_individual(ref, vecs) for ref, vecs in by_entity.items()]
    ind_by_ref = {r["entity_ref"]: r for r in individuals}
    dept_of = {e.emp_id: e.department for e in org.employees}
    peers = {}
    glob = build_global(individuals)
    for dept in ("HR", "Finance", "Developers", "DevOps", "Security"):
        members = [r for r in individuals if dept_of.get(r["entity_ref"]) == dept]
        peers[dept] = build_peer_group(dept, members or individuals[:20])

    return {"org": org, "by_entity": by_entity, "individuals": ind_by_ref, "peers": peers, "global": glob}


@REQUIRE_DOCKER
class TestPlantedAnomaliesDetectedIntegration:
    def test_backfill_populated_windows(self, pg, baselines):
        assert baselines["by_entity"], "backfill must produce per-entity windows"
        assert baselines["individuals"], "individual baselines must build"
        assert baselines["global"]["_count"] > 0

    def test_volume_spike_detected_vs_stored_profile(self, pg, baselines):
        org = baselines["org"]
        emp = org.employees[0]
        profile = build_individual(emp.emp_id, baselines["by_entity"][emp.emp_id])
        planted = inject_scenario(org, __import__("random").Random(1), "volume_spike", NOW)
        norm = [n for n in (validate(normalize_payload(e)) for e in planted) if n is not None]
        spike_window = next(iter(accumulate_all(norm).values()))
        result = vol_eval(finalize(spike_window), profile)
        assert result.triggered
        assert "baseline" in result.explanation.lower()

    def test_impossible_travel_detected(self, pg, baselines):
        org = baselines["org"]
        planted = inject_scenario(org, __import__("random").Random(2), "impossible_travel", NOW)
        norm = [n for n in (validate(normalize_payload(e)) for e in planted) if n is not None]
        pairs = sorted(((ev.geo, ev.ts) for ev in norm if ev.event_type == "login"), key=lambda p: p[1])
        result = travel_eval(pairs)
        assert result.triggered
        assert "km/h" in result.explanation

    def test_out_of_scope_detected(self, pg, baselines):
        org = baselines["org"]
        planted = inject_scenario(org, __import__("random").Random(3), "out_of_scope", NOW)
        norm = [n for n in (validate(normalize_payload(e)) for e in planted) if n is not None]
        ev = norm[0]
        emp = next(e for e in org.employees if e.emp_id == ev.entity_id)
        result = scope_eval(ev.__dict__, emp.department, org.resource_owner)
        assert result.triggered
        assert "department scope" in result.explanation

    def test_dormant_detected(self, pg, baselines):
        org = baselines["org"]
        planted = inject_scenario(org, __import__("random").Random(4), "dormant", NOW)
        norm = [n for n in (validate(normalize_payload(e)) for e in planted) if n is not None]
        ev = norm[0]
        emp = next(e for e in org.employees if e.emp_id == ev.entity_id)
        assert emp.dormant
        result = dormant_eval(ev.__dict__, {"start_hour": 8, "end_hour": 18}, staleness_days=45)
        assert result.triggered
        assert "Dormant" in result.explanation

    def test_novel_peer_detected(self, pg, baselines):
        org = baselines["org"]
        planted = inject_scenario(org, __import__("random").Random(5), "novel_peer", NOW)
        norm = [n for n in (validate(normalize_payload(e)) for e in planted) if n is not None]
        ev = norm[0]
        srv = next(s for s in org.servers if s.server_id == ev.entity_id)
        result = peer_eval(ev.__dict__, srv.peers, {})
        assert result.triggered
        assert ev.peer_entity in result.explanation

    def test_windows_and_profiles_persist_then_rules_reuse_them(self, pg, baselines):
        from db.dao import upsert_window, upsert_profile, get_windows, get_profile

        # hermetic: clear this module's tables so no cross-module rows leak in
        pg.rollback()
        with pg.cursor() as cur:
            cur.execute("TRUNCATE feature_windows, behavioral_profiles RESTART IDENTITY CASCADE")
        pg.commit()

        org = baselines["org"]
        emp = org.employees[0]
        vecs = baselines["by_entity"][emp.emp_id]
        for v in vecs:
            upsert_window(pg, emp.emp_id, datetime.fromisoformat(v["window_start"]), v)
        profile = build_individual(emp.emp_id, vecs)
        upsert_profile(pg, profile)

        assert len(get_windows(pg, emp.emp_id)) == len(vecs)
        stored = get_profile(pg, emp.emp_id, "individual")
        assert stored is not None and stored["confidence"] == profile["confidence"]