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