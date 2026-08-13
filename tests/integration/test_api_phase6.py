"""Phase 6 — API layer integration tests (FastAPI TestClient + real Postgres).

Proves the Phase 6 gate:
  * JWT login works against seeded accounts; wrong password -> 401;
    analyst CANNOT call admin endpoints (role separation) -> 403;
  * every dashboard read endpoint returns real data: /overview, /users,
    /entities, /users/{id}/risk drill-down, /alerts, /incidents;
  * incident lifecycle via PATCH (assign -> investigate -> close) persists;
  * response actions via POST /incidents/{id}/actions are audited;
  * evidence replay (GET /incidents/{id}/evidence) returns full event bodies;
  * admin creates accounts and tunes thresholds (persisted in `settings`).

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
from fastapi.testclient import TestClient

from simulator.org import generate_org
from simulator.engine import run_backfill
from simulator.anomaly import inject_scenario
from streaming.producer import normalize_payload
from analytics.processor import validate
from analytics.features import accumulate_all, finalize
from analytics.ml import train, clear_models
from analytics.runner import AnalyticsRunner

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
    reason="Docker daemon not reachable — Phase 6 integration tests need Docker",
)


def _compose(args):
    return subprocess.run(["docker", "compose", *args], cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=240)


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
        pytest.skip("Docker daemon not reachable — Phase 6 integration tests need Docker")
    _compose(["up", "-d", "postgres", "kafka"])
    deadline = time.time() + 180
    while time.time() < deadline and not (_healthy("postgres") and _healthy("kafka")):
        time.sleep(5)
    proc = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "db/alembic.ini", "upgrade", "head"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
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


@pytest.fixture(scope="module")
def client(pg, engine):
    """Seed org + accounts once, then bind TestClient to the same conn."""
    from db.seed import seed_org, seed_accounts

    conn = pg
    with conn.cursor() as cur:
        cur.execute(
            "TRUNCATE settings, alerts, incidents, analyst_actions, feature_windows, "
            "behavioral_profiles, raw_events, ground_truth, users_accounts, entities, "
            "users, peer_groups RESTART IDENTITY CASCADE"
        )
        conn.commit()

    seed_org(conn, engine["org"])
    seed_accounts(conn, {"analyst": "analyst", "admin": "admin"})

    from api.main import app
    from api.dependencies import get_db

    def _override():
        yield conn

    app.dependency_overrides[get_db] = _override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


def _login(client, username, password):
    return client.post("/auth/login", json={"username": username, "password": password})


def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _truncate(conn):
    conn.rollback()
    with conn.cursor() as cur:
        cur.execute(
            "TRUNCATE settings, alerts, incidents, analyst_actions, feature_windows, "
            "behavioral_profiles, raw_events, ground_truth RESTART IDENTITY CASCADE"
        )
    conn.commit()


@REQUIRE_DOCKER
class TestAuth:
    def test_login_as_analyst_and_admin(self, client):
        for username in ("analyst", "admin"):
            resp = _login(client, username, username)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert "access" in body and "refresh" in body

    def test_wrong_password_401(self, client):
        resp = _login(client, "analyst", "wrong")
        assert resp.status_code == 401

    def test_unknown_user_401(self, client):
        resp = _login(client, "ghost", "ghost")
        assert resp.status_code == 401

    def test_refresh_exchanges_for_access(self, client):
        token = _login(client, "analyst", "analyst").json()["refresh"]
        resp = client.post("/auth/refresh", json={"refresh": token})
        assert resp.status_code == 200
        assert "access" in resp.json()

    def test_no_token_401(self, client):
        assert client.get("/overview").status_code == 401

    def test_analyst_cannot_call_admin_endpoints(self, client):
        token = _login(client, "analyst", "analyst").json()["access"]
        headers = _auth_headers(token)
        assert client.get("/admin/users", headers=headers).status_code == 403
        resp = client.post("/admin/users", headers=headers,
                           json={"username": "bob", "role": "analyst", "password": "pass1234"})
        assert resp.status_code == 403


@REQUIRE_DOCKER
class TestReadEndpoints:
    def test_overview_shape(self, client):
        token = _login(client, "analyst", "analyst").json()["access"]
        resp = client.get("/overview", headers=_auth_headers(token))
        assert resp.status_code == 200
        body = resp.json()
        for key in ("total_risk", "by_band", "top_users", "top_entities", "open_alerts", "open_incidents"):
            assert key in body, body

    def test_users_and_entities_list_real_data(self, client):
        token = _login(client, "analyst", "analyst").json()["access"]
        users = client.get("/users", headers=_auth_headers(token)).json()
        entities = client.get("/entities", headers=_auth_headers(token)).json()
        assert users and all("emp_id" in u for u in users)
        assert entities and all("entity_id" in e for e in entities)

    def test_user_risk_drill_down(self, client, engine):
        token = _login(client, "analyst", "analyst").json()["access"]
        emp_id = engine["org"].employees[0].emp_id
        resp = client.get(f"/users/{emp_id}/risk", headers=_auth_headers(token))
        assert resp.status_code == 200
        body = resp.json()
        assert set(body) >= {"current", "history", "explanation", "baseline_snapshot"}
        assert set(body["current"]) >= {"risk", "band"}
        assert isinstance(body["history"], list)

    def test_user_risk_404_for_unknown(self, client):
        token = _login(client, "analyst", "analyst").json()["access"]
        assert client.get("/users/NOBODY/risk", headers=_auth_headers(token)).status_code == 404


@REQUIRE_DOCKER
class TestIncidentWorkflow:
    def _make_incident(self, client, pg):
        """Seed a runner-produced incident + its raw evidence events."""
        from db.dao import insert_event

        _truncate(pg)
        org = generate_org(seed=42)
        events = [n for n in (validate(normalize_payload(e)) for e in
                              run_backfill(org, days=30, events_per_day=12, seed=42)) if n]
        by_entity = defaultdict(list)
        for w in accumulate_all(events).values():
            by_entity[w.entity_ref].append(finalize(w))
        clear_models()
        train("global", "__global__", [v for vs in by_entity.values() for v in vs], force=True)
        runner = AnalyticsRunner(store=pg, org=org, history=dict(by_entity))
        planted = inject_scenario(org, random.Random(7), "compromise_chain", NOW)
        for e in planted:
            payload = normalize_payload(e)
            insert_event(pg, payload)  # the streaming persist path writes raw_events
            runner.on_event(payload)
        runner.flush()
        from db.dao import get_incidents
        return get_incidents(pg, status="open")[0]

    def test_full_incident_workflow_and_evidence(self, client, pg):
        from db.dao import get_incidents, get_alert

        token = _login(client, "analyst", "analyst").json()["access"]
        headers = _auth_headers(token)
        row = self._make_incident(client, pg)
        incident_id = row["id"]

        listed = client.get("/incidents", headers=headers).json()
        assert any(i["id"] == incident_id for i in listed)

        # assign -> investigate -> close resolved
        resp = client.patch(f"/incidents/{incident_id}", headers=headers, json={"status": "assigned", "assignee": "bob"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "assigned"
        assert resp.json()["assigned_to"] == "bob"

        resp = client.patch(f"/incidents/{incident_id}", headers=headers, json={"status": "investigating"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "investigating"

        # response actions are audited
        for action in ("force_mfa", "revoke_session", "isolate_device"):
            resp = client.post(f"/incidents/{incident_id}/actions", headers=headers, json={"action": action})
            assert resp.status_code == 200, resp.text
            assert resp.json()["status"] == "applied(simulated)"
        trail = client.get(f"/incidents/{incident_id}/actions", headers=headers).json()
        assert {a["action"] for a in trail} == {"force_mfa", "revoke_session", "isolate_device"}

        # note + close
        resp = client.post(f"/incidents/{incident_id}/notes", headers=headers, json={"text": "response complete"})
        assert resp.status_code == 200
        resp = client.patch(f"/incidents/{incident_id}", headers=headers, json={"status": "resolved"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "resolved"

        # evidence replay returns full event bodies for the evidence ids
        resp = client.get(f"/incidents/{incident_id}/evidence", headers=headers)
        assert resp.status_code == 200
        bodies = resp.json()
        assert len(bodies) == len(row["evidence_refs"])
        assert all("event_id" in b and "ts" in b for b in bodies)

    def test_invalid_action_422(self, client, pg):
        token = _login(client, "analyst", "analyst").json()["access"]
        headers = _auth_headers(token)
        row = self._make_incident(client, pg)
        resp = client.post(f"/incidents/{row['id']}/actions", headers=headers, json={"action": "self_destruct"})
        assert resp.status_code == 422

    def test_alerts_patch(self, client, pg):
        from db.dao import insert_alert

        token = _login(client, "analyst", "analyst").json()["access"]
        headers = _auth_headers(token)
        _truncate(pg)
        alert_id = insert_alert(pg, "EMP1", "High", 76, ["ev-1"], status="open")

        listed = client.get("/alerts", headers=headers).json()
        assert any(a["id"] == alert_id for a in listed)

        resp = client.patch(f"/alerts/{alert_id}", headers=headers, json={"status": "assigned", "assignee": "bob"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "assigned"
        assert resp.json()["assigned_to"] == "bob"

        resp = client.patch(f"/alerts/{alert_id}", headers=headers, json={"status": "resolved"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "resolved"

    def test_filter_alerts_by_status_and_band(self, client, pg):
        from db.dao import insert_alert

        token = _login(client, "analyst", "analyst").json()["access"]
        headers = _auth_headers(token)
        _truncate(pg)
        insert_alert(pg, "EMP1", "High", 80, [], status="open")
        insert_alert(pg, "EMP2", "Medium", 40, [], status="resolved")

        open_alerts = client.get("/alerts?status=open", headers=headers).json()
        assert len(open_alerts) == 1 and open_alerts[0]["entity_ref"] == "EMP1"
        band_alerts = client.get("/alerts?band=Medium", headers=headers).json()
        assert len(band_alerts) == 1 and band_alerts[0]["entity_ref"] == "EMP2"


@REQUIRE_DOCKER
class TestAdmin:
    def test_admin_creates_account_and_manages_thresholds(self, client):
        token = _login(client, "admin", "admin").json()["access"]
        headers = _auth_headers(token)

        resp = client.post("/admin/users", headers=headers,
                           json={"username": "soc2", "role": "analyst", "password": "pass1234"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["role"] == "analyst"

        # new analyst can log in
        assert _login(client, "soc2", "pass1234").status_code == 200

        # duplicate rejected
        resp = client.post("/admin/users", headers=headers,
                           json={"username": "soc2", "role": "admin", "password": "pass1234"})
        assert resp.status_code == 409

        # invalid role rejected
        resp = client.post("/admin/users", headers=headers,
                           json={"username": "soc3", "role": "boss", "password": "pass1234"})
        assert resp.status_code == 422

        # thresholds persist into `settings`
        resp = client.put("/admin/thresholds", headers=headers,
                          json={"k": 6.0, "dormancy_days": 45, "band_critical": 88})
        assert resp.status_code == 200
        settings = resp.json()["settings"]
        assert settings["RULE_VOLUME_K"] == 6.0
        assert settings["DORMANCY_DAYS"] == 45
        assert settings["RISK_BAND_CRITICAL"] == 88

        resp = client.get("/admin/thresholds", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["settings"]["RULE_VOLUME_K"] == 6.0