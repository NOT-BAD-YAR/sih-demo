"""Correlation Engine — 4.7 Entity-chain clustering into incident timelines.

Risk-scored events (ScoredEvent) within a rolling window (default 30 min) are
folded into incidents across entity boundaries:

  * resolve_chain(ev) — the graph links [actor, source_entity, target_entity,
    peer_entity] of one event (non-empty for any link that forms an edge);
  * score_event(ev, risk, ...) — wrap a NormalizedEvent + its composed Risk
    into the ScoredEvent the engine consumes;
  * cluster_for_entity(entity_ref, window_events, open_incidents) — open or
    escalate an incident when the window spans >= CHAIN_MIN_LINKS distinct
    chain entities (a "chain" across entities) OR a single event is Critical
    (risk >= CRITICAL_THRESHOLD);
  * maintain_incident(inc, ev) — append evidence, recompute chain + max risk.

The multi-stage Account Compromise sequence folds into ONE incident because
consecutive events share the `actor` and `peer_entity` / `target_entity` edges
within minutes — event-by-event `cluster_for_entity` merges each one into the
already-open incident through the shared chain, even across entity boundaries.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Iterable, Optional

from .risk import band_of

ROLLING_WINDOW = timedelta(minutes=30)
CHAIN_MIN_LINKS = 2      # window touches >= 2 distinct chain entities => correlate
CRITICAL_THRESHOLD = 75.0  # a single Critical risk-scored event opens an incident

CHAIN_ATTRS = ("actor", "source_entity", "target_entity", "peer_entity")

OPEN_STATUSES = ("open", "assigned", "investigating")


@dataclass
class ScoredEvent:
    """A risk-scored event that may join an incident."""

    event_id: str
    entity_ref: str
    ts: datetime
    risk: float            # 0-100
    severity: float        # rule severity 0-1
    chain: list[str]
    alert_id: Optional[str] = None

    @property
    def band(self) -> str:
        return band_of(self.risk)


@dataclass
class Incident:
    id: Optional[int] = None
    entity_ref: Optional[str] = None
    severity: Optional[str] = None
    risk: Optional[int] = None
    status: str = "open"
    entity_chain: list[str] = field(default_factory=list)
    related_alert_ids: list[str] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    notes: dict = field(default_factory=dict)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    assigned_to: Optional[str] = None
    updated_by: Optional[str] = None

    def row(self) -> dict:
        """Serializable incident row (mirrors the `incidents` table)."""
        return {
            "entity_ref": self.entity_ref,
            "severity": self.severity,
            "risk": self.risk,
            "status": self.status,
            "entity_chain": list(self.entity_chain),
            "related_alert_ids": list(self.related_alert_ids),
            "evidence_refs": list(self.evidence_refs),
            "notes": dict(self.notes),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "assigned_to": self.assigned_to,
            "updated_by": self.updated_by,
        }


def _get(ev: Any, key: str) -> Any:
    if isinstance(ev, dict):
        return ev.get(key) or None
    return getattr(ev, key, None) or None


def resolve_chain(ev: Any) -> list[str]:
    """Graph-correlation chain of one event, non-empty for links.

    Returns [actor, source_entity, target_entity, peer_entity] in that order,
    deduplicated. Works on NormalizedEvent (its `.chain` property), ScoredEvent,
    or plain dicts.
    """
    if not isinstance(ev, dict):
        chain = getattr(ev, "chain", None)
        if isinstance(chain, list) and chain:
            seen: list[str] = []
            for item in chain:
                if item not in seen:
                    seen.append(item)
            return seen
    seen = []
    for attr in CHAIN_ATTRS:
        value = _get(ev, attr)
        if value and value not in seen:
            seen.append(value)
    return seen


def score_event(ev: Any, risk: float, severity: float = 0.0, alert_id: Optional[str] = None) -> ScoredEvent:
    """Wrap a normalized event + composed risk into a ScoredEvent."""
    return ScoredEvent(
        event_id=_get(ev, "event_id"),
        entity_ref=_get(ev, "entity_id"),
        ts=_get(ev, "ts"),
        risk=float(risk),
        severity=float(severity),
        chain=resolve_chain(ev),
        alert_id=alert_id,
    )


def _is_open(inc: Incident) -> bool:
    return inc.status in OPEN_STATUSES


def _find_merge_target(chain_members: set[str], open_incidents: Iterable[Incident]) -> Optional[Incident]:
    """First open incident whose entity chain intersects the window's chain."""
    for inc in open_incidents:
        if not _is_open(inc):
            continue
        if set(inc.entity_chain or ()) & chain_members:
            return inc
    return None


def maintain_incident(inc: Incident, ev: ScoredEvent) -> Incident:
    """Append one scored event's evidence and recompute chain + max risk."""
    if ev.event_id and ev.event_id not in inc.evidence_refs:
        inc.evidence_refs.append(ev.event_id)
    if ev.alert_id and ev.alert_id not in inc.related_alert_ids:
        inc.related_alert_ids.append(ev.alert_id)
    for member in ev.chain:
        if member not in inc.entity_chain:
            inc.entity_chain.append(member)
    if inc.entity_chain:
        inc.entity_chain.sort()
    risk = max(float(inc.risk or 0.0), ev.risk)
    inc.risk = int(round(risk))
    inc.severity = band_of(risk)
    if inc.updated_at is None or ev.ts > inc.updated_at:
        inc.updated_at = ev.ts
    return inc


def cluster_for_entity(
    entity_ref: str,
    window_events: list[ScoredEvent],
    open_incidents: Iterable[Incident],
) -> Optional[Incident]:
    """Correlate one entity's risk-scored window into an incident (new/escalated).

    Returns an Incident when the window spans >= CHAIN_MIN_LINKS distinct chain
    entities, or holds a single Critical event. Otherwise returns None. An
    existing open incident sharing any chain entity is escalated in place —
    that is how a multi-stage chain folds into ONE incident across entities.
    """
    if not window_events:
        return None

    chain_members: set[str] = set()
    max_risk = 0.0
    earliest = None
    for ev in window_events:
        chain_members.update(ev.chain)
        max_risk = max(max_risk, ev.risk)
        if earliest is None or ev.ts < earliest:
            earliest = ev.ts

    if len(chain_members) < CHAIN_MIN_LINKS and max_risk < CRITICAL_THRESHOLD:
        return None

    target = _find_merge_target(chain_members, open_incidents)
    if target is None:
        target = Incident(
            entity_ref=entity_ref,
            status="open",
            risk=int(round(max_risk)),
            severity=band_of(max_risk),
            created_at=earliest,
            updated_at=earliest,
        )
    for ev in window_events:
        maintain_incident(target, ev)
    return target


__all__ = [
    "ScoredEvent",
    "Incident",
    "ROLLING_WINDOW",
    "CHAIN_MIN_LINKS",
    "CRITICAL_THRESHOLD",
    "resolve_chain",
    "score_event",
    "maintain_incident",
    "cluster_for_entity",
]