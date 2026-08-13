"""Phase 4F — Runner orchestration integration tests (real Postgres + simulator).

Drives the AnalyticsRunner with a bound database store, proving the Phase 4F
gate end-to-end:

  * the multi-stage Account Compromise chain plus a planted THREAT-DEVICE
    spike folds into ONE persisted incident — across entity boundaries — with
    every rule-triggered event persisted as an alert and the escalation
    reflected in the `incidents` row via `update_incident`;
  * `cron()` rebuilds individual/peer-group/global baselines from the stored
    feature windows and retrains the ML models (LLD 4.8 daily job);
  * `update_incident` round-trips an escalated incident row.

Skipped with an explicit reason when Docker is unavailable.
"""

import json
import random
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from simulator.org import generate_org
from simulator.engine import run_backfill
from simulator.anomaly import inject_scenario
from simulator.schema import build_event
from streaming.producer import normalize_payload
from analytics.processor import validate
from analytics.features import accumulate_all, finalize
from analytics.ml import train, clear_models
from analytics.runner import AnalyticsRunner, cron

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
    reason="Docker daemon not reachable — Phase 4F integration tests need Docker",
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
        pytest.skip("Docker daemon not reachable — Phase 4F integration tests need Docker")
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
    org = generate_org(seed=42)
    events = run_backfill(org, days=30, events_per_day=12, seed=42)
    norm = [n for n in (validate(normalize_payload(e)) for e in events) if n is not None]

    by_entity: dict[str, list[dict]] = defaultdict(list)
    for w in accumulate_all(norm).values():
        by_entity[w.entity_ref].append(finalize(w))
    return {"org": org, "by_entity": dict(by_entity)}


@pytest.fixture(autouse=True)
def _train_global(engine):
    clear_models()
    all_windows = [v for vecs in engine["by_entity"].values() for v in vecs]
    assert train("global", "__global__", all_windows, force=True) is True
    yield
    clear_models()


def _truncate(conn):
    conn.rollback()
    with conn.cursor() as cur:
        cur.execute("TRUNCATE alerts, incidents, feature_windows, behavioral_profiles RESTART IDENTITY CASCADE")
    conn.commit()


def _device_history():
    return [
        {
            "entity_ref": "THREAT-DEVICE",
            "window_start": (NOW - timedelta(days=30 - i)).isoformat(),
            "volume": 50_000_000,
            "event_count": 12,
            "active_hours_frac": 0.5,
            "unique_peers": [],
            "new_peer_count": 0,
            "location_count": 1,
            "location_dist_km": 0.0,
            "dept_distinct": [],
            "sensitivity_hist": {"internal": 12},
            "fail_rate": 0.0,
            "staleness_days": 0,
        }
        for i in range(30)
    ]


@REQUIRE_DOCKER
class TestRunnerPersistence:
    def test_compromise_chain_and_device_spike_fold_into_one_persisted_incident(self, pg, engine):
        from db.dao import get_incidents

        _truncate(pg)
        org = engine["org"]
        history = dict(engine["by_entity"])
        history["THREAT-DEVICE"] = _device_history()

        runner = AnalyticsRunner(store=pg, org=org, history=history)

        planted = inject_scenario(org, random.Random(7), "compromise_chain", NOW)
        for e in planted:
            runner.on_event(normalize_payload(e))

        emp = next(e for e in org.employees if e.emp_id == planted[0].entity_id)
        big_usb = build_event(
            entity_type="device", entity_id="THREAT-DEVICE", actor="THREAT-DEVICE",
            source_entity="THREAT-DEVICE", target_entity=emp.device_id, event_type="usb",
            ts=NOW + timedelta(minutes=15), bytes_moved=int(2 * 1024 ** 3),
            sensitivity="confidential", event_id="big-usb-1",
        )
        runner.on_event(normalize_payload(big_usb))
        runner.flush()

        # one incident, escalated by the device's volume spike, all alerts persisted
        assert runner.stats["incidents"] == 1
        assert runner.stats["escalations"] == 1
        assert runner.stats["alerts"] == 4, runner.stats

        stored = get_incidents(pg, status="open")
        assert len(stored) == 1, stored
        inc_row = stored[0]
        chain = set(inc_row["entity_chain"])
        assert planted[0].entity_id in chain
        assert "THREAT-DEVICE" in chain
        assert "STORAGE.EXTERNAL.CLOUD" in chain
        assert len(inc_row["evidence_refs"]) == 4, inc_row["evidence_refs"]
        assert inc_row["severity"] in {"High", "Critical"}
        assert inc_row["risk"] >= 50.0

    def test_update_incident_roundtrips_escalation(self, pg, engine):
        from db.dao import insert_incident, update_incident, get_incidents

        _truncate(pg)
        from analytics.correlation import Incident

        stamp = datetime.now(timezone.utc)
        inc = Incident(entity_ref="u1", risk=40, severity="Medium",
                       created_at=stamp, updated_at=stamp)
        row_id = insert_incident(pg, inc.row())
        assert isinstance(row_id, int)

        inc.id = row_id
        inc.risk = 88
        inc.severity = "Critical"
        inc.evidence_refs.append("extra-evidence")
        update_incident(pg, {**inc.row(), "id": inc.id})

        stored = {r["id"]: r for r in get_incidents(pg)}
        row = stored[row_id]
        assert row["risk"] == 88
        assert row["severity"] == "Critical"
        assert "extra-evidence" in row["evidence_refs"]


@REQUIRE_DOCKER
class TestCronRebuild:
    def test_cron_rebuilds_baselines_and_retrains_ml(self, pg, engine):
        from db.dao import upsert_window, get_windows, get_profile, upsert_profile

        _truncate(pg)
        org = engine["org"]
        emp = org.employees[0]
        by_entity = engine["by_entity"]

        for i, w in enumerate(by_entity[emp.emp_id][:30]):
            upsert_window(pg, emp.emp_id, datetime.fromisoformat(w["window_start"]), w)
        assert len(get_windows(pg, emp.emp_id)) == 30

        # idempotent upsert — same bucket never duplicates
        upsert_window(pg, emp.emp_id, datetime.fromisoformat(by_entity[emp.emp_id][0]["window_start"]),
                      by_entity[emp.emp_id][0])
        assert len(get_windows(pg, emp.emp_id)) == 30

        # a deployed individual model exists (from a prior run) and is refreshed
        from analytics.ml import train as ml_train
        assert ml_train("individual", emp.emp_id, by_entity[emp.emp_id][:30], force=True) is True

        summary = cron(pg, org, [emp.emp_id], last_n_days=30)
        assert summary["individuals"] == 1
        assert summary["peer_groups"] == 1
        assert summary["global"] is True
        assert summary["ml_retrained"], summary

        ind = get_profile(pg, emp.emp_id, "individual")
        peer = get_profile(pg, emp.emp_id, "peer_group")
        gbl = get_profile(pg, emp.emp_id, "global")
        assert ind is not None and (ind.get("feature_stats") or {})
        assert peer is not None and (peer.get("feature_stats") or {})
        assert gbl is not None and (gbl.get("feature_stats") or {})

    def test_cron_with_no_windows_does_not_crash(self, pg, engine):
        from db.dao import upsert_profile

        _truncate(pg)
        summary = cron(pg, engine["org"], ["EMP-NOPE"], last_n_days=30)
        assert summary["individuals"] == 0
        assert summary["global"] is False