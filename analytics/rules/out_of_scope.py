"""Out-of-scope access rule  -  canonical anomaly #3.

Triggers when a user accesses a resource owned by a department outside their
own. The resource is resolved from the event's `target_entity` or `file_path`
(which is `/owning_dept/resource/...`), then checked against the owning
department's allowed-set for that user.
"""

from __future__ import annotations

from . import RuleResult, not_triggered

RULE_NAME = "out_of_scope"

SEVERITY = 0.8


def _resource_from_event(ev: dict) -> tuple[str, str | None]:
    """Resolve (resource, owning_dept) from target_entity or file_path."""
    target = (ev.get("target_entity") or "").strip()
    if target:
        return target, None

    path = ev.get("file_path") or ""
    parts = [p for p in path.strip("/").split("/") if p]
    if len(parts) >= 2:
        return parts[1], parts[0]  # (resource, owning_dept from path prefix)
    return path, parts[0] if parts else None


def evaluate(
    ev: dict,
    user_dept: str,
    resource_owner: dict[str, str],
    evidence: list[str] | None = None,
) -> RuleResult:
    """Detect access to a resource owned outside the user's department."""
    evidence = evidence or []
    resource, dept_from_path = _resource_from_event(ev)

    owning_dept = dept_from_path or resource_owner.get(resource)
    if not owning_dept:
        return not_triggered(RULE_NAME)

    if owning_dept == user_dept:
        return not_triggered(RULE_NAME)

    explanation = (
        f"{ev.get('entity_id', '?')} accessed {resource}, which is owned by "
        f"{owning_dept}  -  outside their {user_dept} department scope"
    )
    return RuleResult(RULE_NAME, True, SEVERITY, explanation, evidence)