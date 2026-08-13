"""Incident endpoints â€” list, lifecycle, actions, notes, evidence replay (Phase 6)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from analytics.correlation import Incident
from analytics.lifecycle import add_note, transition
from analytics.response import apply as apply_action
from analytics.response import list_actions
from db.dao import get_events_by_ids, get_incident, get_incidents, update_incident
from db.dao import row_to_wire
from ..dependencies import get_db, require_role

router = APIRouter(prefix="/incidents", tags=["incidents"])

_ACTION_BY_STATUS = {
    "assigned": "assign",
    "investigating": "investigate",
    "resolved": "resolve",
    "false_positive": "mark_false_positive",
}


class IncidentPatch(BaseModel):
    status: str | None = None
    assignee: str | None = None


class ActionRequest(BaseModel):
    action: str


class NoteRequest(BaseModel):
    text: str


def _from_row(row: dict) -> Incident:
    return Incident(
        id=row["id"], entity_ref=row["entity_ref"], severity=row["severity"],
        risk=row["risk"], status=row["status"],
        entity_chain=list(row.get("entity_chain") or []),
        related_alert_ids=list(row.get("related_alert_ids") or []),
        evidence_refs=list(row.get("evidence_refs") or []),
        notes=dict(row.get("notes") or {}),
        created_at=row["created_at"], updated_at=row["updated_at"],
        assigned_to=row.get("assigned_to"), updated_by=row.get("updated_by"),
    )


@router.get("")
def list_incidents(
    status: str | None = None,
    assignee: str | None = None,
    conn=Depends(get_db),
    _user: dict = Depends(require_role("analyst", "admin")),
) -> list[dict]:
    """Incident list, filterable by status/assignee."""
    incidents = get_incidents(conn, status=status)
    if assignee:
        incidents = [i for i in incidents if i.get("assigned_to") == assignee]
    return incidents


@router.patch("/{incident_id}")
def patch_incident(
    incident_id: int,
    body: IncidentPatch,
    conn=Depends(get_db),
    user: dict = Depends(require_role("analyst", "admin")),
) -> dict:
    """Advance an incident lifecycle: assign / investigate / close."""
    row = get_incident(conn, incident_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"incident {incident_id} not found")

    inc = _from_row(row)
    next_status = body.status or inc.status
    if next_status not in ("open", "assigned", "investigating", "resolved", "false_positive"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"invalid status {next_status!r}")

    if next_status != inc.status:
        action = _ACTION_BY_STATUS.get(next_status)
        if action is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"cannot move to {next_status!r}")
        try:
            transition(inc, action, user["username"],
                       analyst_id=body.assignee or inc.assigned_to)
        except ValueError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))
    elif body.assignee:
        inc.assigned_to = body.assignee
        inc.updated_by = user["username"]

    update_incident(conn, {**inc.row(), "id": inc.id})
    return get_incident(conn, incident_id)


@router.get("/{incident_id}/evidence")
def incident_evidence(
    incident_id: int,
    conn=Depends(get_db),
    _user: dict = Depends(require_role("analyst", "admin")),
) -> list[dict]:
    """Replay the raw events behind an incident (full wire bodies)."""
    row = get_incident(conn, incident_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"incident {incident_id} not found")
    ids = list(row.get("evidence_refs") or [])
    return [row_to_wire(r) for r in get_events_by_ids(conn, ids)]


@router.post("/{incident_id}/actions")
def create_action(
    incident_id: int,
    body: ActionRequest,
    conn=Depends(get_db),
    user: dict = Depends(require_role("analyst", "admin")),
) -> dict:
    """Apply a simulated response action (audited in analyst_actions)."""
    row = get_incident(conn, incident_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"incident {incident_id} not found")
    try:
        return apply_action(
            conn, incident_id, body.action, actor=user["username"],
            entity_chain=list(row.get("entity_chain") or []),
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))


@router.get("/{incident_id}/actions")
def incident_actions(
    incident_id: int,
    conn=Depends(get_db),
    _user: dict = Depends(require_role("analyst", "admin")),
) -> list[dict]:
    """Audit trail for one incident."""
    return list_actions(conn, incident_id=incident_id)


@router.post("/{incident_id}/notes")
def create_note(
    incident_id: int,
    body: NoteRequest,
    conn=Depends(get_db),
    user: dict = Depends(require_role("analyst", "admin")),
) -> dict:
    """Append an analyst note to an incident."""
    row = get_incident(conn, incident_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"incident {incident_id} not found")
    if not body.text:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "note text required")
    inc = _from_row(row)
    add_note(inc, user["username"], body.text)
    update_incident(conn, {**inc.row(), "id": inc.id})
    return {"incident_id": incident_id, "note": inc.notes["entries"][-1]}