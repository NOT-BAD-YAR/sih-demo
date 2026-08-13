"""Overview endpoint — live SOC summary (Phase 6)."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from analytics.lifecycle import TERMINAL_STATES
from db.dao import get_alerts, get_incidents
from ..dependencies import get_db, require_role

router = APIRouter(prefix="/overview", tags=["overview"])

_TOP_N = 5


def _top_by_risk(rows: list[dict], n: int = _TOP_N) -> list[dict]:
    by_entity: dict[str, dict] = {}
    for r in rows:
        if r.get("risk") is None:
            continue
        ref = r.get("entity_ref")
        if not ref:
            continue
        cur = by_entity.get(ref)
        if cur is None or r["risk"] > cur["risk"]:
            by_entity[ref] = {"entity_ref": ref, "risk": r["risk"], "severity": r.get("severity")}
    return sorted(by_entity.values(), key=lambda x: -x["risk"])[:n]


@router.get("")
def overview(conn=Depends(get_db), _user: dict = Depends(require_role("analyst", "admin"))) -> dict:
    """Aggregate risk/alert counters for the dashboard Overview screen."""
    incidents = get_incidents(conn)
    alerts = get_alerts(conn)
    open_incidents = [i for i in incidents if i["status"] not in TERMINAL_STATES]
    open_alerts = [a for a in alerts if a["status"] not in TERMINAL_STATES]

    by_band: dict[str, int] = {}
    for i in open_incidents:
        band = i.get("severity") or "unknown"
        by_band[band] = by_band.get(band, 0) + 1

    all_open = open_incidents + open_alerts
    return {
        "total_risk": round(sum(float(i["risk"]) for i in open_incidents if i.get("risk") is not None), 2),
        "by_band": by_band,
        "top_users": _top_by_risk(all_open),
        "top_entities": _top_by_risk(all_open),
        "open_alerts": len(open_alerts),
        "open_incidents": len(open_incidents),
    }