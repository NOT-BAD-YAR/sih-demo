"""Phase 4E — Correlation Engine integration tests (real Postgres + simulator).

Proves the 4E gate end-to-end:
  * the multi-stage Account Compromise sequence (login@new-loc -> new device
    -> sensitive access -> big download -> external upload) is scored through
    the FULL composed pipeline (rules + ML + context + risk) and then FOLDS
    into ONE incident — across entity boundaries, because consecutive events
    share the actor / THREAT-DEVICE / peer edges within one 30-min window;
  * every scored event stays bounded with a valid band;
  * replaying the window into the open incident never duplicates evidence;
  * the folded incident (entity_chain, evidence_refs, related_alert_ids,
    severity) round-trips through the real `alerts` / `incidents` tables.

Skipped with an explicit reason when Docker is unavailable.
"""

import json
import random
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

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
from analytics.risk import compute, fuse, impact as impact_fn, band_of
from analytics.correlation import cluster_for_entity, score_event
from analytics.rules.out_of_scope import evaluate as scope
from analytics.rules.volume_spike import evaluate as vol
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
    reason="Docker daemon not reachable — Phase 4E integration tests need Docker",
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
        pytest.skip("Docker daemon not reachable — Phase 4E integration tests need Docker")
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


def _score_chain(engine, rng_seed: int = 7):
    """Score every event of the planted compromise_chain through the pipeline."""
    org = engine["org"]
    by_entity = engine["by_entity"]

    planted = inject_scenario(org, random.Random(rng_seed), "compromise_chain", NOW)
    norm = [n for n in (validate(normalize_payload(e)) for e in planted) if n]
    assert len(norm) == 5, f"compromise_chain must plant 5 events, got {len(norm)}"

    buckets = {}
    for w in accumulate_all(norm).values():
        buckets[w.entity_ref] = finalize(w)

    emp_id = norm[0].entity_id
    actor = _find_actor(org, emp_id)
    assert actor is not None
    profile = build_individual(emp_id, by_entity[emp_id])
    win = buckets[emp_id]
    ml_emp = score("global", "__global__", win)

    scored = []
    for ev in norm:
        is_emp = ev.entity_id == emp_id
        result = None
        if ev.event_type == "file_access":
            result = scope(ev.__dict__, actor.department, org.resource_owner)
        elif ev.event_type == "upload":
            result = peer(ev.__dict__, set(), {})
        elif ev.event_type == "download":
            result = vol(win, profile)
        sev = result.severity if result else 0.0

        if is_emp:
            ml = ml_emp
            ctx = ctx_build(ev, profile, actor, org.resource_owner)
        else:
            dev_win = buckets.get(ev.entity_id)
            ml = score("global", "__global__", dev_win) if dev_win else 0.0
            ctx = ctx_build(ev, None, None, org.resource_owner)

        risk = compute(
            fuse([sev], ml),
            impact_fn(ctx.target_sensitivity, ctx.role_factor, ctx.dept_factor),
            ctx.baseline_confidence,
            rule_bonus=sev * 0.1,
            components={"rules": [sev], "ml": ml},
        )
        scored.append(score_event(ev, risk.risk_100, severity=sev, alert_id=f"ALERT-{ev.event_id}"))
    return norm, scored, emp_id


@REQUIRE_DOCKER
class TestCorrelationPipeline:
    def test_compromise_chain_folds_into_one_incident(self, pg, engine):
        norm, scored, emp_id = _score_chain(engine)
        emp_events = [se for se in scored if se.entity_ref == emp_id]
        device_events = [se for se in scored if se.entity_ref != emp_id]
        assert len(emp_events) == 4 and len(device_events) == 1

        incident = cluster_for_entity(emp_id, emp_events, [])
        assert incident is not None
        folded = cluster_for_entity(device_events[0].entity_ref, device_events, [incident])
        assert folded is incident  # same incident, escalated across entity boundary

        assert len(incident.evidence_refs) == 5, incident.evidence_refs
        assert len(incident.related_alert_ids) == 5
        assert set(incident.evidence_refs) == {ev.event_id for ev in norm}
        chain = set(incident.entity_chain)
        assert emp_id in chain
        assert "THREAT-DEVICE" in chain
        assert "STORAGE.EXTERNAL.CLOUD" in chain
        assert incident.risk >= 50.0, incident.risk  # real exfil chain must not be trivial
        assert incident.severity in {"High", "Critical"}

    def test_scored_events_bounded_with_valid_bands(self, pg, engine):
        _, scored, _ = _score_chain(engine)
        for se in scored:
            assert 0.0 <= se.risk <= 100.0, se
            assert se.band in BANDS, se
            assert se.chain, f"scored event must carry a non-empty chain: {se.event_id}"

    def test_replay_never_duplicates_evidence(self, pg, engine):
        norm, scored, emp_id = _score_chain(engine)
        incident = cluster_for_entity(emp_id, scored, [])
        assert incident is not None
        replayed = cluster_for_entity(emp_id, scored, [incident])
        assert replayed is incident
        assert len(incident.evidence_refs) == len(norm)
        assert len(incident.related_alert_ids) == len(norm)

    def test_incident_persists_and_roundtrips(self, pg, engine):
        from db.dao import insert_alert, insert_incident, get_incidents

        pg.rollback()
        with pg.cursor() as cur:
            cur.execute("TRUNCATE alerts, incidents RESTART IDENTITY CASCADE")
        pg.commit()

        _, scored, emp_id = _score_chain(engine)
        incident = cluster_for_entity(emp_id, scored, [])
        assert incident is not None

        alert_ids = []
        for se in scored:
            alert_ids.append(insert_alert(pg, se.entity_ref, se.band, int(round(se.risk)), [se.event_id]))
        incident.related_alert_ids = [str(a) for a in alert_ids]

        row = incident.row()
        row_id = insert_incident(pg, row)
        assert isinstance(row_id, int)

        stored = {r["id"]: r for r in get_incidents(pg, status="open")}
        assert row_id in stored
        inc_row = stored[row_id]
        assert set(inc_row["entity_chain"]) == set(incident.entity_chain)
        assert len(inc_row["evidence_refs"]) == 5
        assert len(inc_row["related_alert_ids"]) == 5
        assert inc_row["severity"] == incident.severity
        assert inc_row["risk"] == incident.risk