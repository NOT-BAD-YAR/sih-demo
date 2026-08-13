"""Users / Entities endpoints + risk drill-down (Phase 6)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from db.dao import get_alerts, get_entities, get_incidents, get_users
from ..dependencies import get_db, require_role
from ..risk_view import entity_risk_view

router = APIRouter(tags=["entities"])


def _attach_risk(conn, rows: list[dict], key: str) -> list[dict]:
    """Attach max open risk per entity (from persisted alerts/incidents)."""
    open_rows = [r for r in get_incidents(conn) + get_alerts(conn) if r["status"] not in
                 ("resolved", "false_positive") and r.get("risk") is not None]
    best: dict[str, float] = {}
    for r in open_rows:
        ref = r.get("entity_ref")
        if ref and r["risk"] > best.get(ref, -1.0):
            best[ref] = float(r["risk"])
    for row in rows:
        ref = row.get(key)
        row["risk"] = best.get(ref)
    return rows


@router.get("/users")
def list_users(
    search: str | None = None,
    dept: str | None = None,
    conn=Depends(get_db),
    _user: dict = Depends(require_role("analyst", "admin")),
) -> list[dict]:
    """People with live risk, filterable by search/dept."""
    return _attach_risk(conn, get_users(conn, search=search, dept=dept), "emp_id")


@router.get("/entities")
def list_entities(
    kind: str | None = None,
    search: str | None = None,
    conn=Depends(get_db),
    _user: dict = Depends(require_role("analyst", "admin")),
) -> list[dict]:
    """Devices/servers/apps with live risk."""
    return _attach_risk(conn, get_entities(conn, kind=kind, search=search), "entity_id")


def _risk_drill_down(conn, entity_ref: str) -> dict:
    return entity_risk_view(conn, entity_ref)


@router.get("/users/{entity_id}/risk")
def user_risk(
    entity_id: str,
    conn=Depends(get_db),
    _user: dict = Depends(require_role("analyst", "admin")),
) -> dict:
    """Risk drill-down: normal -> current -> deviation -> risk -> explanation."""
    users = get_users(conn, search=entity_id)
    if not any(u["emp_id"] == entity_id for u in users):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"user {entity_id!r} not found")
    return _risk_drill_down(conn, entity_id)


@router.get("/entities/{entity_id}/risk")
def entity_risk(
    entity_id: str,
    conn=Depends(get_db),
    _user: dict = Depends(require_role("analyst", "admin")),
) -> dict:
    """Risk drill-down for a device/server/app."""
    entities = get_entities(conn, search=entity_id)
    if not any(e["entity_id"] == entity_id for e in entities):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"entity {entity_id!r} not found")
    return _risk_drill_down(conn, entity_id)