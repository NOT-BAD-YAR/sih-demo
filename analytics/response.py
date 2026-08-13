"""Response engine — recommended + simulated actions  —  Phase 5.

Response actions are always **recommended / simulated**: there is no real
sysadmin integration. Applying an action:

  * validates the action against the audited enum,
  * records it in `analyst_actions` with `status='applied(simulated)'`,
  * stores an `impact` summary and a `simulated_state` JSONB side-effect so
    the dashboard can show the consequence (e.g. the entity "isolated").

The playbook maps each detection type to its recommended action list.
"""

from __future__ import annotations

from datetime import datetime, timezone

ACTIONS = (
    "force_mfa",
    "revoke_session",
    "restrict_access",
    "isolate_device",
    "notify_manager",
    "investigate",
)

PLAYBOOK: dict[str, list[str]] = {
    "impossible_travel": ["force_mfa", "revoke_session"],
    "volume_spike": ["restrict_access", "notify_manager"],
    "out_of_scope": ["revoke_session", "restrict_access"],
    "dormant": ["force_mfa", "notify_manager"],
    "novel_peer": ["isolate_device", "investigate"],
    "chain": ["force_mfa", "revoke_session", "isolate_device"],
}

STATUS_SIMULATED = "applied(simulated)"


def recommend(alert_type: str) -> list[str]:
    """Recommended action list for a detection type (empty when unknown)."""
    return list(PLAYBOOK.get(alert_type, []))


def simulate(entity_chain: list[str] | None, action: str) -> dict:
    """Pure side-effect the simulated action would produce (JSONB-able)."""
    chain = sorted(set(entity_chain or []))
    side_effects = {
        "force_mfa": {"mfa_forced": True, "next_login_requires": "mfa"},
        "revoke_session": {"sessions_revoked": True},
        "restrict_access": {"access_restricted_to": "managed_assets"},
        "isolate_device": {"isolated_entity": chain},
        "notify_manager": {"manager_notified": True},
        "investigate": {"investigation_opened": True},
    }
    return dict(side_effects.get(action, {"simulated": True}))


def apply(
    conn,
    incident_id: int,
    action: str,
    actor: str,
    *,
    alert_type: str | None = None,
    entity_chain: list[str] | None = None,
    now: datetime | None = None,
) -> dict:
    """Apply (simulate) one response action and audit it in `analyst_actions`.

    Raises ValueError for an action outside the audited enum.
    Returns the audited action row.
    """
    if action not in ACTIONS:
        raise ValueError(
            f"unknown response action {action!r}; expected one of {ACTIONS}"
        )
    from db.dao import insert_action

    now = now or datetime.now(timezone.utc)
    impact = {
        "action": action,
        "target": int(incident_id),
        "actor": actor,
        "alert_type": alert_type,
        "ts": now.isoformat(),
        "simulated": True,
    }
    simulated_state = simulate(entity_chain, action)
    action_id = insert_action(
        conn,
        int(incident_id),
        action,
        actor,
        impact=impact,
        status=STATUS_SIMULATED,
        simulated_state=simulated_state,
    )
    return {
        "id": action_id,
        "incident_id": int(incident_id),
        "action": action,
        "actor_user": actor,
        "status": STATUS_SIMULATED,
        "impact": impact,
        "simulated_state": simulated_state,
    }


def list_actions(conn, incident_id: int | None = None) -> list[dict]:
    """Audit trail of applied actions (optionally scoped to one incident)."""
    from db.dao import list_actions as _dao_list_actions

    return _dao_list_actions(conn, incident_id=incident_id)


__all__ = [
    "ACTIONS",
    "PLAYBOOK",
    "STATUS_SIMULATED",
    "recommend",
    "simulate",
    "apply",
    "list_actions",
]