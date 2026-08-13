"""Phase 5 — Alert/Incident/Response engine integration tests (real Postgres).

Proves the Phase 5 gate end-to-end:

  * escalation tiering persists: `escalate` + `create_alert`/`to_incident`
    produce a DB alert/incident row carrying evidence + assignment;
  * the full incident lifecycle (assign -> investigate -> resolve) round-trips
    through `update_incident`, persisting `assigned_to` + `updated_by`;
  * `analyst_actions` audit trail: `apply`/`list_actions` record simulated
    response actions (status `applied(simulated)` + JSONB simulated_state);
  * a runner-produced incident can be assigned, actioned per the chain
    playbook, and closed — one audited workflow from detection to response.

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
from streaming.producer import normalize_payload
from analytics.processor import validate
from analytics.features import accumulate_all, finalize
from analytics.ml import train, clear_models
from analytics.runner import AnalyticsRunner
from analytics.correlation import Incident

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
    reason="Docker daemon not reachable — Phase 5 integration tests need Docker",
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
        pytest.skip("Docker daemon not reachable — Phase 5 integration tests need Docker")
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
        cur.execute("TRUNCATE alerts, incidents, analyst_actions, feature_windows, behavioral_profiles RESTART IDENTITY CASCADE")
    conn.commit()


def _incident_from_row(row: dict) -> Incident:
    return Incident(
        id=row["id"],
        entity_ref=row["entity_ref"],
        severity=row["severity"],
        risk=row["risk"],
        status=row["status"],
        entity_chain=list(row.get("entity_chain") or []),
        related_alert_ids=list(row.get("related_alert_ids") or []),
        evidence_refs=list(row.get("evidence_refs") or []),
        notes=dict(row.get("notes") or {}),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        assigned_to=row.get("assigned_to"),
        updated_by=row.get("updated_by"),
    )


@REQUIRE_DOCKER
class TestEscalationPersistence:
    def test_alert_persists_and_is_readable(self, pg):
        from db.dao import insert_alert, get_alert, get_alerts

        _truncate(pg)
        alert_id = insert_alert(
            pg, "EMP1", "High", 76,
            evidence_refs=["ev-1", "ev-2"], status="open",
        )
        assert isinstance(alert_id, int)

        row = get_alert(pg, alert_id)
        assert row is not None
        assert row["entity_ref"] == "EMP1"
        assert row["severity"] == "High"
        assert row["risk"] == 76
        assert row["status"] == "open"
        assert row["evidence_refs"] == ["ev-1", "ev-2"]

        open_alerts = get_alerts(pg, status="open")
        assert any(a["id"] == alert_id for a in open_alerts)

    def test_escalated_incident_carries_assignment_and_audit_fields(self, pg):
        from db.dao import insert_incident, get_incident
        from analytics.lifecycle import create_alert, escalate, to_incident

        _truncate(pg)
        band, sensitivity = "High", "restricted"
        alert_level, incident_needed = escalate(band, sensitivity)
        assert alert_level == "incident" and incident_needed is True

        stamp = datetime.now(timezone.utc)
        alert = create_alert("EMP7", 82, band, evidence_refs=["ev-a"], incident_needed=True, creator="alice", now=stamp)
        incident = to_incident(alert)

        row_id = insert_incident(pg, incident.row())
        stored = get_incident(pg, row_id)
        assert stored is not None
        assert stored["severity"] == "High"
        assert stored["risk"] == 82
        assert stored["evidence_refs"] == ["ev-a"]
        assert stored["assigned_to"] == "alice"
        assert stored["updated_by"] == "alice"


@REQUIRE_DOCKER
class TestIncidentLifecycleRoundtrip:
    def test_assign_investigate_resolve_persists_assignment_and_auditor(self, pg):
        from db.dao import insert_incident, get_incident, update_incident
        from analytics.lifecycle import assign, investigate, close

        _truncate(pg)
        stamp = datetime.now(timezone.utc)
        inc = Incident(entity_ref="EMP2", severity="Critical", risk=91,
                       created_at=stamp, updated_at=stamp)
        row_id = insert_incident(pg, inc.row())
        inc.id = row_id

        assign(inc, analyst_id="bob", actor="alice", now=stamp + timedelta(minutes=1))
        update_incident(pg, {**inc.row(), "id": inc.id})
        stored = get_incident(pg, row_id)
        assert stored["status"] == "assigned"
        assert stored["assigned_to"] == "bob"
        assert stored["updated_by"] == "alice"

        investigate(inc, actor="bob", now=stamp + timedelta(minutes=2))
        update_incident(pg, {**inc.row(), "id": inc.id})
        assert get_incident(pg, row_id)["status"] == "investigating"

        close(inc, "resolved", actor="bob", now=stamp + timedelta(minutes=5))
        update_incident(pg, {**inc.row(), "id": inc.id})
        stored = get_incident(pg, row_id)
        assert stored["status"] == "resolved"
        assert stored["updated_by"] == "bob"

    def test_alert_lifecycle_roundtrip(self, pg):
        from db.dao import insert_alert, get_alert, update_alert_status

        _truncate(pg)
        alert_id = insert_alert(pg, "EMP3", "Medium", 40, evidence_refs=[], status="open")
        update_alert_status(pg, alert_id, "resolved", updated_by="bob")
        stored = get_alert(pg, alert_id)
        assert stored["status"] == "resolved"
        assert stored["updated_by"] == "bob"


@REQUIRE_DOCKER
class TestResponseAudit:
    def test_apply_records_simulated_actions(self, pg):
        from db.dao import insert_incident
        from analytics.response import ACTIONS, apply, list_actions, recommend, simulate

        _truncate(pg)
        stamp = datetime.now(timezone.utc)
        inc = Incident(entity_ref="EMP4", severity="Critical", risk=95,
                       entity_chain=["EMP4", "DEV-9", "STORAGE.EXTERNAL.CLOUD"],
                       created_at=stamp, updated_at=stamp)
        row_id = insert_incident(pg, inc.row())

        chain = ["EMP4", "DEV-9", "STORAGE.EXTERNAL.CLOUD"]
        for action in recommend("chain"):
            assert action in ACTIONS
            result = apply(pg, row_id, action, actor="alice", alert_type="chain", entity_chain=chain)
            assert result["status"] == "applied(simulated)"
            assert result["impact"]["simulated"] is True

        trail = list_actions(pg, incident_id=row_id)
        assert len(trail) == 3, trail
        assert all(a["status"] == "applied(simulated)" for a in trail)
        assert all(a["actor_user"] == "alice" for a in trail)

        by_action = {a["action"]: a for a in trail}
        assert by_action["isolate_device"]["simulated_state"]["isolated_entity"] == [
            "DEV-9", "EMP4", "STORAGE.EXTERNAL.CLOUD"
        ]

    def test_apply_rejects_unknown_action_before_touching_db(self, pg):
        from analytics.response import apply

        with pytest.raises(ValueError):
            apply(pg, 1, "self_destruct", "alice")


@REQUIRE_DOCKER
class TestRunnerToResponseWorkflow:
    def test_runner_incident_full_response_cycle(self, pg, engine):
        from db.dao import get_incidents, update_incident
        from analytics.lifecycle import assign, investigate, close, add_note
        from analytics.response import apply, list_actions

        _truncate(pg)
        org = engine["org"]
        history = dict(engine["by_entity"])
        runner = AnalyticsRunner(store=pg, org=org, history=history)

        planted = inject_scenario(org, random.Random(7), "compromise_chain", NOW)
        for e in planted:
            runner.on_event(normalize_payload(e))
        runner.flush()

        assert runner.stats["incidents"] == 1, runner.stats
        stored = get_incidents(pg, status="open")
        assert len(stored) == 1
        inc = _incident_from_row(stored[0])
        inc.id = stored[0]["id"]

        assign(inc, analyst_id="bob", actor="alice")
        update_incident(pg, {**inc.row(), "id": inc.id})
        investigate(inc, actor="bob")
        update_incident(pg, {**inc.row(), "id": inc.id})

        for action in ["force_mfa", "revoke_session", "isolate_device"]:
            apply(pg, inc.id, action, actor="bob", alert_type="chain", entity_chain=inc.entity_chain)

        add_note(inc, analyst_id="bob", text="response complete, closing")
        close(inc, "resolved", actor="bob")
        update_incident(pg, {**inc.row(), "id": inc.id})

        final = get_incidents(pg)
        assert len(final) == 1
        row = final[0]
        assert row["status"] == "resolved"
        assert row["assigned_to"] == "bob"
        assert row["updated_by"] == "bob"
        assert row["notes"]["entries"][0]["text"] == "response complete, closing"

        trail = list_actions(pg, incident_id=inc.id)
        assert len(trail) == 3
        assert {a["action"] for a in trail} == {"force_mfa", "revoke_session", "isolate_device"}