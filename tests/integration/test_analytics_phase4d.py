"""Phase 4D — Context + Risk Engine integration tests (real Postgres + simulator).

Proves the 4D gate against live data:
  - every planted anomaly runs the full composed pipeline
    (rule severity + global IF signal → anomaly fusion → context factors →
    Risk = Anomaly × Impact × Confidence) and yields a bounded, valid-band
    risk score;
  - strong anomalies (volume spike, impossible travel, out-of-scope) land at
    High/Critical, while LOW-confidence cold-start entities (dormant,
    novel-peer) are judged gently (never harshly High/Critical);
  - sparse entities score lower than rich entities for the same anomaly
    (cold-start gentleness), results are reproducible, out-of-scope raises
    impact, and risk is recomputable from persisted Postgres windows.

Skipped with an explicit reason when Docker is unavailable.
"""

import subprocess
import sys
import time
import json
import random
import statistics
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
from analytics.context import build as ctx_build
from analytics.risk import compute, fuse, impact as impact_fn
from analytics.rules.volume_spike import evaluate as vol
from analytics.rules.impossible_travel import evaluate as travel
from analytics.rules.out_of_scope import evaluate as scope
from analytics.rules.dormant import evaluate as dorm
from analytics.rules.novel_peer import evaluate as peer

ROOT = Path(__file__).resolve().parent.parent.parent
pytestmark = pytest.mark.integration

DSN = "postgresql://ueba:ueba_secret@localhost:5432/ueba"
NOW = datetime(2026, 2, 1, 10, 30, tzinfo=timezone.utc)
BANDS = {"Low", "Medium", "High", "Critical"}


def _docker_available() -> bool:
    try:
        subprocess.run(["docker", "info"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=15)
        return True
    except Exception:
        return False


REQUIRE_DOCKER = pytest.mark.skipif(
    not _docker_available(),
    reason="Docker daemon not reachable — Phase 4D integration tests need Docker",
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
        pytest.skip("Docker daemon not reachable — Phase 4D integration tests need Docker")
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
def engine():
    """Org backfill → per-entity windows + a trained global IF model."""
    org = generate_org(seed=42)
    events = run_backfill(org, days=30, events_per_day=12, seed=42)
    norm = [n for n in (validate(normalize_payload(e)) for e in events) if n is not None]

    by_entity: dict[str, list[dict]] = defaultdict(list)
    for w in accumulate_all(norm).values():
        by_entity[w.entity_ref].append(finalize(w))

    clear_models()
    all_windows = [v for vecs in by_entity.values() for v in vecs]
    assert train("global", "__global__", all_windows, force=True) is True
    return {"org": org, "by_entity": dict(by_entity)}


@pytest.fixture(autouse=True)
def _clean_cache():
    clear_models()
    yield
    clear_models()


def _find_actor(org, entity_id):
    for e in org.employees:
        if e.emp_id == entity_id:
            return e
    for s in org.servers:
        if s.server_id == entity_id:
            return s
    return None


def _compose_risk(engine, name: str, rng_seed: int = 7):
    """Run one planted anomaly through rule + ML + context + risk."""
    org = engine["org"]
    by_entity = engine["by_entity"]

    planted = inject_scenario(org, random.Random(rng_seed), name, NOW)
    norm = [n for n in (validate(normalize_payload(e)) for e in planted) if n]
    assert norm, f"{name}: planted events must normalize"
    ev = norm[0]
    actor = _find_actor(org, ev.entity_id)

    profile = None
    if ev.entity_id in by_entity:
        profile = build_individual(ev.entity_id, by_entity[ev.entity_id])

    win = finalize(next(iter(accumulate_all(norm).values())))
    ml = score("global", "__global__", win)

    if name == "volume_spike":
        result = vol(win, profile)
    elif name == "impossible_travel":
        pairs = sorted(((e.geo, e.ts) for e in norm if e.event_type == "login"), key=lambda p: p[1])
        result = travel(pairs)
    elif name == "out_of_scope":
        result = scope(ev.__dict__, actor.department, org.resource_owner)
    elif name == "dormant":
        result = dorm(ev.__dict__, {"start_hour": 8, "end_hour": 18}, staleness_days=45)
    else:
        srv = next(s for s in org.servers if s.server_id == ev.entity_id)
        result = peer(ev.__dict__, srv.peers, {})

    ctx = ctx_build(ev, profile, actor, org.resource_owner)
    anomaly = fuse([result.severity], ml)
    impact = impact_fn(ctx.target_sensitivity, ctx.role_factor, ctx.dept_factor)
    risk = compute(anomaly, impact, ctx.baseline_confidence,
                   rule_bonus=result.severity * 0.1,
                   components={"rules": [result.severity], "ml": ml})
    return {"name": name, "sev": result.severity, "ml": ml, "anomaly": anomaly,
            "impact": impact, "confidence": ctx.baseline_confidence,
            "risk": risk, "ctx": ctx}


@REQUIRE_DOCKER
class TestRiskPipeline:
    def test_all_planted_anomalies_produce_bounded_defensible_risk(self, pg, engine):
        for name in ("volume_spike", "impossible_travel", "out_of_scope", "dormant", "novel_peer"):
            out = _compose_risk(engine, name)
            assert 0.0 <= out["risk"].risk_100 <= 100.0, name
            assert out["risk"].band in BANDS, name
            assert out["risk"].breakdown["anomaly"] == pytest.approx(out["anomaly"])
            assert out["risk"].breakdown["components"]["rules"] == [out["sev"]]

    def test_strong_anomalies_are_high_or_critical(self, pg, engine):
        for name in ("volume_spike", "impossible_travel", "out_of_scope"):
            out = _compose_risk(engine, name)
            assert out["risk"].risk_100 >= 50.0, f"{name} should be High/Critical, got {out['risk'].breakdown}"

    def test_cold_start_anomalies_judged_gently(self, pg, engine):
        for name in ("dormant", "novel_peer"):
            out = _compose_risk(engine, name)
            assert out["risk"].risk_100 <= 50.0, (
                f"{name} belongs to a LOW-confidence entity and must not be harshly judged, got {out['risk'].breakdown}"
            )
            assert out["confidence"] < 1.0, f"{name} actor should be cold-start (confidence < HIGH)"

    def test_sparse_entity_judged_gently_than_rich(self, pg, engine):
        out = _compose_risk(engine, "volume_spike")
        rich = compute(out["anomaly"], out["impact"], 1.0, rule_bonus=out["sev"] * 0.1)
        sparse = compute(out["anomaly"], out["impact"], 0.4, rule_bonus=out["sev"] * 0.1)
        assert sparse.risk_100 < rich.risk_100
        assert rich.risk_100 >= 50.0  # rich entity with a full-severity rule is High+

    def test_reproducible(self, pg, engine):
        a = _compose_risk(engine, "volume_spike", rng_seed=5)
        b = _compose_risk(engine, "volume_spike", rng_seed=5)
        assert a["risk"].risk_100 == b["risk"].risk_100
        assert a["risk"].breakdown == b["risk"].breakdown

    def test_out_of_scope_raises_impact(self, pg, engine):
        out = _compose_risk(engine, "out_of_scope")
        assert out["ctx"].dept_factor == 1.4
        in_scope = impact_fn(out["ctx"].target_sensitivity, out["ctx"].role_factor, 1.0)
        assert out["impact"] >= in_scope

    def test_risk_recomputed_from_persisted_windows(self, pg, engine):
        from db.dao import upsert_window, get_windows

        pg.rollback()
        with pg.cursor() as cur:
            cur.execute("TRUNCATE feature_windows RESTART IDENTITY CASCADE")
        pg.commit()

        org = engine["org"]
        emp = org.employees[0]
        vecs = engine["by_entity"][emp.emp_id]
        for v in vecs:
            upsert_window(pg, emp.emp_id, datetime.fromisoformat(v["window_start"]), v)

        stored = get_windows(pg, emp.emp_id)
        assert len(stored) == len(vecs)
        rehydrated = [r["vector"] for r in stored]
        profile = build_individual(emp.emp_id, rehydrated)

        planted = inject_scenario(org, random.Random(3), "volume_spike", NOW)
        norm = [n for n in (validate(normalize_payload(e)) for e in planted) if n]
        ev = norm[0]
        win = finalize(next(iter(accumulate_all(norm).values())))
        result = vol(win, profile)
        assert result.triggered

        ml = score("global", "__global__", win)
        ctx = ctx_build(ev, profile, emp, org.resource_owner)
        risk = compute(fuse([result.severity], ml), impact_fn(ctx.target_sensitivity, ctx.role_factor, ctx.dept_factor),
                       ctx.baseline_confidence, rule_bonus=result.severity * 0.1)
        assert risk.risk_100 >= 50.0
        assert risk.band in BANDS