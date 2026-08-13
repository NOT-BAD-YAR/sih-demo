"""Phase 4C — ML Engine (Isolation Forest) integration tests (real Postgres).

Proves the Phase 4C gate against live data:
  - real simulator backfill produces enough windows to train the global and
    peer-group models;
  - an injected volume-spike window scores HIGHER on the 0-1 anomaly signal
    than a normal window (IF emits a supplementary signal, never "malice");
  - sparse entities without an individual model fall back to the peer/global
    model instead of returning nothing (cold start);
  - windows persisted in feature_windows can be read back and re-train a model
    (rolling retrain source of truth).

Skipped with an explicit reason when Docker is unavailable.
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
from analytics.baseline import build_individual
from analytics.ml import train, score, clear_models

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
    reason="Docker daemon not reachable — Phase 4C integration tests need Docker",
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
        pytest.skip("Docker daemon not reachable — Phase 4C integration tests need Docker")
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
def windows_by_entity():
    """Org backfill → per-entity finalized feature windows (no DB needed)."""
    org = generate_org(seed=42)
    events = run_backfill(org, days=30, events_per_day=12, seed=42)
    norm = [n for n in (validate(normalize_payload(e)) for e in events) if n is not None]

    by_entity: dict[str, list[dict]] = defaultdict(list)
    for w in accumulate_all(norm).values():
        by_entity[w.entity_ref].append(finalize(w))
    return {"org": org, "by_entity": dict(by_entity)}


@pytest.fixture(autouse=True)
def _clean_cache():
    clear_models()
    yield
    clear_models()


def _spike_window(org) -> dict:
    planted = inject_scenario(org, __import__("random").Random(1), "volume_spike", NOW)
    norm = [n for n in (validate(normalize_payload(e)) for e in planted) if n is not None]
    w = next(iter(accumulate_all(norm).values()))
    return finalize(w)


@REQUIRE_DOCKER
class TestGlobalModel:
    def test_backfill_produces_windows(self, pg, windows_by_entity):
        assert windows_by_entity["by_entity"], "backfill must produce per-entity windows"

    def test_volume_spike_scores_higher_than_normal(self, pg, windows_by_entity):
        all_windows = [v for vecs in windows_by_entity["by_entity"].values() for v in vecs]
        assert len(all_windows) >= 20
        assert train("global", "__global__", all_windows, force=True) is True

        import statistics
        normal_scores = [score("global", "__global__", w) for w in all_windows]
        normal_median = statistics.median(normal_scores)
        spike_w = _spike_window(windows_by_entity["org"])
        spike = score("global", "__global__", spike_w)

        assert 0.0 <= spike <= 1.0
        assert spike > 0.5, "planted volume spike must be flagged by the IF signal"
        assert spike > normal_median, "spike must score above the typical normal window"

    def test_spike_volume_dominates_the_window(self, pg, windows_by_entity):
        spike_w = _spike_window(windows_by_entity["org"])
        assert spike_w["volume"] > max(w["volume"] for vecs in windows_by_entity["by_entity"].values() for w in vecs)


@REQUIRE_DOCKER
class TestColdStartFallback:
    def test_sparse_entity_uses_peer_model_not_neutral(self, pg, windows_by_entity):
        org = windows_by_entity["org"]
        by_entity = windows_by_entity["by_entity"]

        dept_of = {e.emp_id: e.department for e in org.employees}
        depts = {d for d in dept_of.values()}
        dept = next(iter(depts))
        member_windows = [w for ref, vecs in by_entity.items() if dept_of.get(ref) == dept for w in vecs]
        assert train("peer_group", dept, member_windows, force=True) is True

        sparse_ref = next(iter(ref for ref in by_entity if dept_of.get(ref) != dept))
        sparse_window = by_entity[sparse_ref][0]
        s = score("individual", sparse_ref, sparse_window,
                  fallback_keys=[f"peer_group:{dept}", "global:__global__"])
        assert 0.0 < s <= 1.0, "sparse entity must fall back to the peer model, not neutral 0"

    def test_no_models_returns_neutral_zero(self, pg, windows_by_entity):
        assert score("individual", "GHOST", windows_by_entity["by_entity"][list(windows_by_entity["by_entity"])[0]][0]) == 0.0


@REQUIRE_DOCKER
class TestPersistedWindowsFeedML:
    def test_stored_windows_retrain_model(self, pg, windows_by_entity):
        from db.dao import upsert_window, get_windows

        pg.rollback()
        with pg.cursor() as cur:
            cur.execute("TRUNCATE feature_windows RESTART IDENTITY CASCADE")
        pg.commit()

        org = windows_by_entity["org"]
        emp = org.employees[0]
        vecs = windows_by_entity["by_entity"][emp.emp_id]
        for v in vecs:
            upsert_window(pg, emp.emp_id, datetime.fromisoformat(v["window_start"]), v)

        stored = get_windows(pg, emp.emp_id)
        assert len(stored) == len(vecs)
        rehydrated = [r["vector"] for r in stored]

        assert train("individual", emp.emp_id, rehydrated, force=True) is True
        assert score("individual", emp.emp_id, rehydrated[0]) >= 0.0
        assert score("individual", emp.emp_id, rehydrated[0]) <= 1.0


@REQUIRE_DOCKER
class TestIndividualGateInPipeline:
    def test_entity_below_min_windows_needs_force(self, pg, windows_by_entity):
        ref, vecs = next(iter(windows_by_entity["by_entity"].items()))
        assert train("individual", ref, vecs) is False or len(vecs) >= 20, (
            "an entity below ML_MIN_WINDOWS must not silently get its own model"
        )