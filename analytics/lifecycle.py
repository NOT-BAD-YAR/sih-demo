"""Alert/Incident lifecycle + escalation tiering  —  Phase 5.

Turns risk output into managed alerts and incidents with a team workflow:

    escalate(band, sensitivity)  ->  (alert_level, incident_needed)
    create_alert(...) / to_incident(alert)   ->  escalation
    assign / investigate / close / add_note  ->  lifecycle transitions
    role_can(role, action)                   ->  RBAC guard shared by the API

Escalation tiering (LLD): band + entity sensitivity decide

    Critical always incident
    High + restricted-sensitivity -> incident
    High otherwise -> alert·assigned
    Medium/Low -> open alert (triage)

Lifecycle state machines (the DB CHECK enforces the same states on both
tables):

    ALERT:    open -> assigned -> resolved | false_positive
    INCIDENT: open -> assigned -> investigating -> resolved | false_positive

Every transition stamps `updated_at` + `updated_by` (LLD requirement).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from .correlation import Incident

# States shared by the `alerts` and `incidents` CHECK constraints.
VALID_STATES = ("open", "assigned", "investigating", "resolved", "false_positive")
TERMINAL_STATES = ("resolved", "false_positive")
OPEN_STATES = ("open", "assigned", "investigating")

SENSITIVITY_RESTRICTED = "restricted"

# action -> (from-states, to-state)
_INCIDENT_TRANSITIONS = {
    "assign": (("open",), "assigned"),
    "investigate": (("assigned",), "investigating"),
    "resolve": (("assigned", "investigating"), "resolved"),
    "mark_false_positive": (("assigned", "investigating"), "false_positive"),
}
_ALERT_TRANSITIONS = {
    "assign": (("open",), "assigned"),
    "investigate": (("assigned",), "investigating"),
    "resolve": (("open", "assigned", "investigating"), "resolved"),
    "mark_false_positive": (("open", "assigned", "investigating"), "false_positive"),
}

# RBAC: action -> roles allowed (shared by the Phase 6 API).
ROLE_ACTIONS: dict[str, tuple[str, ...]] = {
    "view": ("analyst", "admin"),
    "assign": ("analyst", "admin"),
    "investigate": ("analyst", "admin"),
    "act": ("analyst", "admin"),
    "close": ("analyst", "admin"),
    "add_note": ("analyst", "admin"),
    "manage": ("admin",),
    "tune_thresholds": ("admin",),
}


def role_can(role: str, action: str) -> bool:
    """RBAC guard: may `role` perform `action`? Unknown roles/actions deny."""
    return action in ROLE_ACTIONS and role in ROLE_ACTIONS[action]


def escalate(band: str, sensitivity: str) -> tuple[str, bool]:
    """Escalation tiering → (alert_level, incident_needed).

    alert_level ∈ {"open", "assigned", "incident"}.
    """
    if band == "Critical":
        return "incident", True
    if band == "High" and sensitivity == SENSITIVITY_RESTRICTED:
        return "incident", True
    if band == "High":
        return "assigned", False
    return "open", False


@dataclass
class Alert:
    """Managed alert row (mirrors the `alerts` table)."""

    entity_ref: str
    risk: int
    severity: str
    evidence_refs: list[str] = field(default_factory=list)
    id: Optional[int] = None
    status: str = "open"
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    updated_by: Optional[str] = None
    assigned_to: Optional[str] = None

    def row(self) -> dict:
        return {
            "entity_ref": self.entity_ref,
            "severity": self.severity,
            "risk": self.risk,
            "status": self.status,
            "evidence_refs": list(self.evidence_refs),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "updated_by": self.updated_by,
            "assigned_to": self.assigned_to,
        }


def _now() -> datetime:
    return datetime.now(timezone.utc)


def create_alert(
    entity_ref: str,
    risk: float,
    band: str,
    evidence_refs: list[str] | None = None,
    incident_needed: bool = False,
    creator: str | None = None,
    now: Optional[datetime] = None,
) -> Alert:
    """Create a managed alert; pre-assigned when it needs immediate triage."""
    now = now or _now()
    return Alert(
        entity_ref=entity_ref,
        risk=int(round(risk)),
        severity=band,
        status="assigned" if incident_needed else "open",
        evidence_refs=list(evidence_refs or []),
        created_at=now,
        updated_at=now,
        updated_by=creator,
        assigned_to=creator if incident_needed else None,
    )


def to_incident(alert: Alert, now: Optional[datetime] = None) -> Incident:
    """Escalate an alert into an incident (shares evidence + assignment)."""
    now = now or _now()
    return Incident(
        entity_ref=alert.entity_ref,
        severity=alert.severity,
        risk=alert.risk,
        status="open",
        evidence_refs=list(alert.evidence_refs),
        created_at=alert.created_at or now,
        updated_at=now,
        updated_by=alert.updated_by,
        assigned_to=alert.assigned_to,
    )


def _transitions_for(obj) -> dict:
    if isinstance(obj, Alert):
        return _ALERT_TRANSITIONS
    if isinstance(obj, Incident):
        return _INCIDENT_TRANSITIONS
    raise TypeError(f"unsupported lifecycle object: {type(obj).__name__}")


def transition(
    obj,
    action: str,
    actor: str,
    analyst_id: str | None = None,
    now: Optional[datetime] = None,
):
    """Apply one lifecycle transition; raises ValueError on an invalid move.

    `assign` requires `analyst_id` (the person the work is handed to).
    Mutates and returns the same object.
    """
    table = _transitions_for(obj)
    if action not in table:
        raise ValueError(f"unknown lifecycle action {action!r}")
    allowed, next_state = table[action]
    if obj.status not in allowed:
        raise ValueError(f"cannot {action!r} a {type(obj).__name__.lower()} in state {obj.status!r}")

    now = now or _now()
    obj.status = next_state
    obj.updated_at = now
    obj.updated_by = actor
    if action == "assign":
        if not analyst_id:
            raise ValueError("assign requires an analyst_id")
        obj.assigned_to = analyst_id
    return obj


def assign(obj, analyst_id: str, actor: str, now: Optional[datetime] = None):
    return transition(obj, "assign", actor, analyst_id=analyst_id, now=now)


def investigate(obj, actor: str, now: Optional[datetime] = None):
    return transition(obj, "investigate", actor, now=now)


def close(obj, verdict: str, actor: str, now: Optional[datetime] = None):
    """Close with a verdict: `resolved` or `false_positive`."""
    if verdict not in ("resolved", "false_positive"):
        raise ValueError(f"invalid close verdict {verdict!r}")
    return transition(obj, "resolve" if verdict == "resolved" else "mark_false_positive", actor, now=now)


def add_note(obj, analyst_id: str, text: str, now: Optional[datetime] = None):
    """Append a human note to the incident notes (audited in `notes`)."""
    if isinstance(obj, Alert):
        raise TypeError("add_note applies to incidents, not alerts")
    if not text:
        raise ValueError("note text required")
    now = now or _now()
    entries = list((obj.notes or {}).get("entries", []))
    entries.append({"by": analyst_id, "ts": now.isoformat(), "text": text})
    obj.notes = {**dict(obj.notes or {}), "entries": entries}
    obj.updated_at = now
    obj.updated_by = analyst_id
    return obj


__all__ = [
    "Alert",
    "VALID_STATES",
    "TERMINAL_STATES",
    "OPEN_STATES",
    "ROLE_ACTIONS",
    "role_can",
    "escalate",
    "create_alert",
    "to_incident",
    "transition",
    "assign",
    "investigate",
    "close",
    "add_note",
]
