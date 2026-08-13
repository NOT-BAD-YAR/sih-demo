"""Alert queue endpoints (Phase 6)."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from db.dao import get_alert, get_alerts, update_alert_status
from ..dependencies import get_db, require_role

router = APIRouter(prefix="/alerts", tags=["alerts"])


class AlertPatch(BaseModel):
    status: str | None = None
    assignee: str | None = None


@router.get("")
def list_alerts(
    status: str | None = None,
    band: str | None = None,
    conn=Depends(get_db),
    _user: dict = Depends(require_role("analyst", "admin")),
) -> list[dict]:
    """Open alert queue, filterable by status/severity band."""
    alerts = get_alerts(conn, status=status)
    if band:
        alerts = [a for a in alerts if (a.get("severity") or "") == band]
    return alerts


@router.patch("/{alert_id}")
def patch_alert(
    alert_id: int,
    body: AlertPatch,
    conn=Depends(get_db),
    user: dict = Depends(require_role("analyst", "admin")),
) -> dict:
    """Advance an alert (assign / investigate / close)."""
    row = get_alert(conn, alert_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"alert {alert_id} not found")

    next_status = body.status or row["status"]
    if next_status not in ("open", "assigned", "investigating", "resolved", "false_positive"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"invalid status {next_status!r}")

    from analytics.lifecycle import Alert
    from analytics.lifecycle import transition as lifecycle_transition

    alert = Alert(
        id=row["id"], entity_ref=row["entity_ref"], severity=row["severity"], risk=row["risk"],
        status=row["status"], evidence_refs=list(row.get("evidence_refs") or []),
        created_at=row["created_at"], updated_at=row["updated_at"],
        updated_by=row.get("updated_by"), assigned_to=row.get("assigned_to"),
    )
    action_by_status = {"assigned": "assign", "investigating": "investigate",
                        "resolved": "resolve", "false_positive": "mark_false_positive"}
    if next_status != alert.status:
        action = action_by_status.get(next_status)
        if action is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"cannot move to {next_status!r}")
        lifecycle_transition(alert, action, user["username"],
                             analyst_id=body.assignee or alert.assigned_to)

    update_alert_status(conn, alert.id, alert.status,
                        updated_by=user["username"],
                        assigned_to=body.assignee if body.assignee else alert.assigned_to,
                        updated_at=datetime.now(timezone.utc))
    return get_alert(conn, alert_id)