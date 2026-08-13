"""Typed repository functions for the UEBA engine and API (Phase 3).

Only Phase 3 delivers these core functions; Phase 4+ adds profile/window and
alert/incident DAOs on top. All functions take an open psycopg2 connection so
callers (engine consumer, FastAPI dependencies) control transactions.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import psycopg2.extras
from psycopg2.extensions import connection as PgConnection

from .conn import dict_cursor

SCHEMA_TABLES = (
    "users",
    "peer_groups",
    "entities",
    "raw_events",
    "behavioral_profiles",
    "feature_windows",
    "alerts",
    "incidents",
    "users_accounts",
    "analyst_actions",
    "ground_truth",
)

_INSERT_EVENT_SQL = """
    INSERT INTO raw_events (
        event_id, ts, ingested_at, entity_type, entity_id, user_id, event_type,
        actor, source_entity, target_entity, peer_entity, ip, geo, file_path,
        bytes, outcome, sensitivity, raw_payload
    ) VALUES (
        %(event_id)s, %(ts)s, %(ingested_at)s, %(entity_type)s, %(entity_id)s,
        %(user_id)s, %(event_type)s, %(actor)s, %(source_entity)s,
        %(target_entity)s, %(peer_entity)s, %(ip)s, %(geo)s, %(file_path)s,
        %(bytes)s, %(outcome)s, %(sensitivity)s, %(raw_payload)s
    )
    ON CONFLICT (event_id) DO NOTHING
"""


def _wire_to_row(payload: dict) -> dict:
    """Map a wire payload (Common Event Schema dict) to a raw_events row."""
    return {
        "event_id": str(payload["event_id"]),
        "ts": _as_datetime(payload.get("ts")),
        "ingested_at": _as_datetime(payload.get("ingested_at")),
        "entity_type": payload.get("entity_type"),
        "entity_id": payload.get("entity_id"),
        "user_id": payload.get("user_id"),
        "event_type": payload["event_type"],
        "actor": payload.get("actor"),
        "source_entity": payload.get("source_entity"),
        "target_entity": payload.get("target_entity"),
        "peer_entity": payload.get("peer_entity"),
        "ip": payload.get("ip"),
        "geo": json.dumps(payload.get("geo")) if payload.get("geo") else None,
        "file_path": payload.get("file_path"),
        "bytes": payload.get("bytes", 0),
        "outcome": payload.get("outcome", "success"),
        "sensitivity": payload.get("sensitivity", "internal"),
        "raw_payload": json.dumps(payload.get("raw_payload")) if payload.get("raw_payload") else None,
    }


def _as_datetime(value: Any) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def _row_to_wire(row: dict) -> dict:
    """Map a raw_events row back to a Common Event Schema dict."""
    out = dict(row)
    for key in ("ts", "ingested_at"):
        if isinstance(out.get(key), datetime):
            out[key] = out[key].isoformat()
    return out


def row_to_wire(row: dict) -> dict:
    """Public wrapper for evidence replay (Phase 6 API)."""
    return _row_to_wire(row)


def insert_event(conn: PgConnection, payload: dict) -> bool:
    """Persist one normalized event; `ON CONFLICT (event_id) DO NOTHING`.

    Returns True when the row was inserted, False when the event_id was
    already present (duplicate redelivery rejected by the DB unique key).
    """
    with conn.cursor() as cur:
        cur.execute(_INSERT_EVENT_SQL, _wire_to_row(payload))
        inserted = cur.rowcount > 0
    conn.commit()
    return inserted


def get_event(conn: PgConnection, event_id: str) -> dict | None:
    """Fetch one raw event by its event_id, or None."""
    with dict_cursor(conn) as cur:
        cur.execute("SELECT * FROM raw_events WHERE event_id = %s", (str(event_id),))
        row = cur.fetchone()
    return _row_to_wire(dict(row)) if row else None


def count_events(conn: PgConnection) -> int:
    with dict_cursor(conn) as cur:
        cur.execute("SELECT count(*) AS n FROM raw_events")
        return int(cur.fetchone()["n"])


def list_recent_events(conn: PgConnection, limit: int = 50) -> list[dict]:
    """Most-recent events ordered by ts desc (dashboard/evidence replay)."""
    with dict_cursor(conn) as cur:
        cur.execute("SELECT * FROM raw_events ORDER BY ts DESC LIMIT %s", (int(limit),))
        return [_row_to_wire(dict(r)) for r in cur.fetchall()]


def events_for_user(conn: PgConnection, user_id: str, limit: int = 100) -> list[dict]:
    """Events tied to a user (uses the (user_id, ts) index)."""
    with dict_cursor(conn) as cur:
        cur.execute(
            "SELECT * FROM raw_events WHERE user_id = %s ORDER BY ts DESC LIMIT %s",
            (user_id, int(limit)),
        )
        return [_row_to_wire(dict(r)) for r in cur.fetchall()]


def schema_tables(conn: PgConnection) -> set[str]:
    """Names of every table in the public schema that we own (Phase 3 gate)."""
    with dict_cursor(conn) as cur:
        cur.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
        )
        return {r["tablename"] for r in cur.fetchall()}


# ---------------------------------------------------------------------------
# Phase 4 — feature windows + behavioural profiles (window/profile DAOs)
# ---------------------------------------------------------------------------

_WINDOW_UPSERT_SQL = """
    INSERT INTO feature_windows (entity_ref, window_start, vector, ts)
    VALUES (%(entity_ref)s, %(window_start)s, %(vector)s, %(ts)s)
    ON CONFLICT (entity_ref, window_start) DO UPDATE
        SET vector = EXCLUDED.vector, ts = EXCLUDED.ts
"""


def upsert_window(
    conn: PgConnection,
    entity_ref: str,
    window_start: datetime,
    vector: dict,
    ts: datetime | None = None,
) -> None:
    """Persist a closed feature window, replacing any existing one for the bucket.

    Requires the Phase 4 unique index on (entity_ref, window_start); kept
    idempotent so redelivered/re-processed windows never duplicate.
    """
    with conn.cursor() as cur:
        cur.execute(
            _WINDOW_UPSERT_SQL,
            {
                "entity_ref": entity_ref,
                "window_start": window_start,
                "vector": json.dumps(vector),
                "ts": ts or window_start,
            },
        )
    conn.commit()


def get_windows(
    conn: PgConnection,
    entity_ref: str,
    since: datetime | None = None,
    limit: int = 5000,
) -> list[dict]:
    """Recent closed windows for an entity, oldest-first (baseline rebuild)."""
    sql = "SELECT * FROM feature_windows WHERE entity_ref = %s"
    params: list[Any] = [entity_ref]
    if since is not None:
        sql += " AND window_start >= %s"
        params.append(since)
    sql += " ORDER BY window_start ASC LIMIT %s"
    params.append(int(limit))
    with dict_cursor(conn) as cur:
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]


_PROFILE_UPSERT_SQL = """
    INSERT INTO behavioral_profiles (
        entity_ref, level, feature_stats, allowed_sets, active_window,
        confidence, updated_to
    ) VALUES (
        %(entity_ref)s, %(level)s, %(feature_stats)s, %(allowed_sets)s,
        %(active_window)s, %(confidence)s, %(updated_to)s
    )
    ON CONFLICT (entity_ref, level) DO UPDATE
        SET feature_stats = EXCLUDED.feature_stats,
            allowed_sets = EXCLUDED.allowed_sets,
            active_window = EXCLUDED.active_window,
            confidence = EXCLUDED.confidence,
            updated_to = EXCLUDED.updated_to
"""


def upsert_profile(conn: PgConnection, row: dict) -> None:
    """Write a behavioural profile row (individual/peer_group/global)."""
    with conn.cursor() as cur:
        cur.execute(
            _PROFILE_UPSERT_SQL,
            {
                "entity_ref": row["entity_ref"],
                "level": row["level"],
                "feature_stats": json.dumps(row.get("feature_stats") or {}),
                "allowed_sets": json.dumps(row.get("allowed_sets") or {}),
                "active_window": json.dumps(row.get("active_window") or {}),
                "confidence": row.get("confidence", "LOW"),
                "updated_to": row.get("updated_to") or datetime.now(timezone.utc),
            },
        )
    conn.commit()


def get_profile(conn: PgConnection, entity_ref: str, level: str) -> dict | None:
    with dict_cursor(conn) as cur:
        cur.execute(
            "SELECT * FROM behavioral_profiles WHERE entity_ref = %s AND level = %s",
            (entity_ref, level),
        )
        row = cur.fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# Phase 4E — alert + incident DAOs (correlation engine persistence)
# ---------------------------------------------------------------------------

def insert_alert(
    conn: PgConnection,
    entity_ref: str,
    severity: str,
    risk: int,
    evidence_refs: list[str],
    status: str = "open",
) -> int:
    """Persist one alert; returns its id."""
    with dict_cursor(conn) as cur:
        cur.execute(
            "INSERT INTO alerts (entity_ref, severity, risk, status, evidence_refs) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (entity_ref, severity, int(risk), status, json.dumps(list(evidence_refs))),
        )
        alert_id = int(cur.fetchone()["id"])
    conn.commit()
    return alert_id


def update_alert_status(
    conn: PgConnection,
    alert_id: int,
    status: str,
    updated_by: str | None = None,
    assigned_to: str | None = None,
    updated_at: datetime | None = None,
) -> None:
    """Advance an alert through its lifecycle, stamping updated_at/updated_by."""
    if updated_at is None:
        updated_at = datetime.now(timezone.utc)
    with dict_cursor(conn) as cur:
        cur.execute(
            "UPDATE alerts SET status = %s, updated_at = %s, updated_by = %s, "
            "assigned_to = COALESCE(%s, assigned_to) WHERE id = %s",
            (status, updated_at, updated_by, assigned_to, int(alert_id)),
        )
    conn.commit()


_INSERT_INCIDENT_SQL = """
    INSERT INTO incidents (
        entity_ref, severity, risk, status, entity_chain, related_alert_ids,
        evidence_refs, notes, created_at, updated_at, assigned_to, updated_by
    ) VALUES (
        %(entity_ref)s, %(severity)s, %(risk)s, %(status)s, %(entity_chain)s,
        %(related_alert_ids)s, %(evidence_refs)s, %(notes)s, %(created_at)s,
        %(updated_at)s, %(assigned_to)s, %(updated_by)s
    )
    RETURNING id
"""


def insert_incident(conn: PgConnection, incident: dict) -> int:
    """Persist an incident row (Incident.row()); returns its id."""
    row = {
        "entity_ref": incident.get("entity_ref"),
        "severity": incident.get("severity"),
        "risk": incident.get("risk"),
        "status": incident.get("status", "open"),
        "entity_chain": json.dumps(incident.get("entity_chain") or []),
        "related_alert_ids": json.dumps(incident.get("related_alert_ids") or []),
        "evidence_refs": json.dumps(incident.get("evidence_refs") or []),
        "notes": json.dumps(incident.get("notes") or {}),
        "created_at": incident.get("created_at"),
        "updated_at": incident.get("updated_at"),
        "assigned_to": incident.get("assigned_to"),
        "updated_by": incident.get("updated_by"),
    }
    with dict_cursor(conn) as cur:
        cur.execute(_INSERT_INCIDENT_SQL, row)
        incident_id = int(cur.fetchone()["id"])
    conn.commit()
    return incident_id


def get_incidents(conn: PgConnection, status: str | None = None) -> list[dict]:
    """Recent incidents, newest-first; optionally filtered by status."""
    sql = "SELECT * FROM incidents"
    params: list[Any] = []
    if status is not None:
        sql += " WHERE status = %s"
        params.append(status)
    sql += " ORDER BY id DESC LIMIT 500"
    with dict_cursor(conn) as cur:
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]


def get_incident(conn: PgConnection, incident_id: int) -> dict | None:
    """One incident row by id (None when absent)."""
    with dict_cursor(conn) as cur:
        cur.execute("SELECT * FROM incidents WHERE id = %s", (int(incident_id),))
        row = cur.fetchone()
    return dict(row) if row else None


def get_alerts(conn: PgConnection, status: str | None = None) -> list[dict]:
    """Recent alerts, newest-first; optionally filtered by status."""
    sql = "SELECT * FROM alerts"
    params: list[Any] = []
    if status is not None:
        sql += " WHERE status = %s"
        params.append(status)
    sql += " ORDER BY id DESC LIMIT 500"
    with dict_cursor(conn) as cur:
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]


def get_alert(conn: PgConnection, alert_id: int) -> dict | None:
    """One alert row by id (None when absent)."""
    with dict_cursor(conn) as cur:
        cur.execute("SELECT * FROM alerts WHERE id = %s", (int(alert_id),))
        row = cur.fetchone()
    return dict(row) if row else None


def update_incident(conn: PgConnection, incident: dict) -> None:
    """Update an existing incident row (severity/risk/escalation evidence).

    Used by the runner when correlation escalates an already-persisted
    incident (new evidence, higher risk, broader entity chain).
    """
    sql = """
        UPDATE incidents
        SET severity = %(severity)s,
            risk = %(risk)s,
            status = %(status)s,
            entity_chain = %(entity_chain)s,
            related_alert_ids = %(related_alert_ids)s,
            evidence_refs = %(evidence_refs)s,
            notes = %(notes)s,
            assigned_to = %(assigned_to)s,
            updated_by = %(updated_by)s,
            updated_at = %(updated_at)s
        WHERE id = %(id)s
    """
    row = {
        "id": incident["id"],
        "severity": incident.get("severity"),
        "risk": incident.get("risk"),
        "status": incident.get("status", "open"),
        "entity_chain": json.dumps(incident.get("entity_chain") or []),
        "related_alert_ids": json.dumps(incident.get("related_alert_ids") or []),
        "evidence_refs": json.dumps(incident.get("evidence_refs") or []),
        "notes": json.dumps(incident.get("notes") or {}),
        "assigned_to": incident.get("assigned_to"),
        "updated_by": incident.get("updated_by"),
        "updated_at": incident.get("updated_at"),
    }
    with dict_cursor(conn) as cur:
        cur.execute(sql, row)
    conn.commit()


# ---------------------------------------------------------------------------
# Phase 5 — analyst_actions audit trail (response engine)
# ---------------------------------------------------------------------------

_INSERT_ACTION_SQL = """
    INSERT INTO analyst_actions (
        incident_id, action, actor_user, impact, status, simulated_state
    ) VALUES (
        %(incident_id)s, %(action)s, %(actor_user)s, %(impact)s, %(status)s,
        %(simulated_state)s
    )
    RETURNING id
"""


def insert_action(
    conn: PgConnection,
    incident_id: int,
    action: str,
    actor_user: str,
    impact: dict | None = None,
    status: str = "applied(simulated)",
    simulated_state: dict | None = None,
) -> int:
    """Audit one analyst response action; returns its id.

    Response actions are always simulated — the `status` records the outcome
    (default `applied(simulated)`) and `simulated_state` carries the JSONB
    side-effect the dashboard shows.
    """
    row = {
        "incident_id": int(incident_id),
        "action": action,
        "actor_user": actor_user,
        "impact": json.dumps(impact or {}),
        "status": status,
        "simulated_state": json.dumps(simulated_state) if simulated_state is not None else None,
    }
    with dict_cursor(conn) as cur:
        cur.execute(_INSERT_ACTION_SQL, row)
        action_id = int(cur.fetchone()["id"])
    conn.commit()
    return action_id


def list_actions(conn: PgConnection, incident_id: int | None = None) -> list[dict]:
    """Audit trail, newest-first; optionally scoped to one incident."""
    sql = "SELECT * FROM analyst_actions"
    params: list[Any] = []
    if incident_id is not None:
        sql += " WHERE incident_id = %s"
        params.append(int(incident_id))
    sql += " ORDER BY id DESC LIMIT 500"
    with dict_cursor(conn) as cur:
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]


# ---------------------------------------------------------------------------
# Phase 6 — API read models (accounts, entities, settings, evidence)
# ---------------------------------------------------------------------------


def get_account_by_username(conn: PgConnection, username: str) -> dict | None:
    """One users_accounts row by username (login)."""
    with dict_cursor(conn) as cur:
        cur.execute("SELECT * FROM users_accounts WHERE username = %s", (str(username),))
        row = cur.fetchone()
    return dict(row) if row else None


def create_account(conn: PgConnection, username: str, password_hash: str, role: str) -> int:
    """Create a dashboard account (admin only); returns its id."""
    with dict_cursor(conn) as cur:
        cur.execute(
            "INSERT INTO users_accounts (username, password_hash, role, disabled) "
            "VALUES (%s, %s, %s, false) RETURNING id",
            (str(username), str(password_hash), str(role)),
        )
        account_id = int(cur.fetchone()["id"])
    conn.commit()
    return account_id


def list_accounts(conn: PgConnection) -> list[dict]:
    """All dashboard accounts (admin user management)."""
    with dict_cursor(conn) as cur:
        cur.execute("SELECT id, username, role, disabled FROM users_accounts ORDER BY id")
        return [dict(r) for r in cur.fetchall()]


def get_users(conn: PgConnection, search: str | None = None, dept: str | None = None) -> list[dict]:
    """People from the `users` table (drives the Users screen)."""
    sql = "SELECT * FROM users"
    clauses: list[str] = []
    params: list[Any] = []
    if search:
        clauses.append("(emp_id ILIKE %s OR name ILIKE %s)")
        params.extend([f"%{search}%", f"%{search}%"])
    if dept:
        clauses.append("department = %s")
        params.append(dept)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY emp_id LIMIT 500"
    with dict_cursor(conn) as cur:
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]


def get_entities(conn: PgConnection, kind: str | None = None, search: str | None = None) -> list[dict]:
    """Devices/servers/apps from the `entities` table (Entities screen)."""
    sql = "SELECT * FROM entities"
    clauses: list[str] = []
    params: list[Any] = []
    if kind:
        clauses.append("kind = %s")
        params.append(kind)
    if search:
        clauses.append("entity_id ILIKE %s")
        params.append(f"%{search}%")
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY entity_id LIMIT 500"
    with dict_cursor(conn) as cur:
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]


def get_events_by_ids(conn: PgConnection, event_ids: list[str]) -> list[dict]:
    """Full raw_events rows for a list of event ids (evidence replay)."""
    if not event_ids:
        return []
    with dict_cursor(conn) as cur:
        cur.execute(
            "SELECT * FROM raw_events WHERE event_id = ANY(%s)",
            (list(event_ids),),
        )
        return [dict(r) for r in cur.fetchall()]


def get_setting(conn: PgConnection, key: str) -> dict | None:
    """One settings row by key."""
    with dict_cursor(conn) as cur:
        cur.execute("SELECT key, value, updated_at FROM settings WHERE key = %s", (str(key),))
        row = cur.fetchone()
    return dict(row) if row else None


def upsert_setting(conn: PgConnection, key: str, value) -> None:
    """Write (or replace) one engine threshold in `settings`."""
    with dict_cursor(conn) as cur:
        cur.execute(
            "INSERT INTO settings (key, value) VALUES (%s, %s) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, "
            "updated_at = now()",
            (str(key), json.dumps(value)),
        )
    conn.commit()


def list_settings(conn: PgConnection) -> list[dict]:
    """All engine thresholds (admin read)."""
    with dict_cursor(conn) as cur:
        cur.execute("SELECT key, value, updated_at FROM settings ORDER BY key")
        return [dict(r) for r in cur.fetchall()]