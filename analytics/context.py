"""Context Engine — 4.5 behavioural context vector per event.

Turns one normalized event + the entity's selected baseline profile + the
acting user into a `ContextVector`: WHO / USING WHAT / DOING WHAT / FROM
WHERE / WHEN / HOW MUCH, plus the 0-1 factors the Risk Engine needs
(target sensitivity, role factor, department scope factor, baseline
confidence weight).

Factor tables are the LLD §4.5 constants — they are the single source of
truth for `analytics.risk`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

# --- 0-1 factor tables (LLD 4.5) -------------------------------------------
SENSITIVITY_SCORE = {
    "public": 0.1,
    "internal": 0.3,
    "confidential": 0.6,
    "restricted": 0.9,
}

ROLE_FACTOR = {
    "admin": 1.0,
    "exec": 0.9,
    "staff": 0.6,
    "contractor": 0.8,
}

CONFIDENCE_WEIGHT = {
    "LOW": 0.4,
    "MED": 0.7,
    "HIGH": 1.0,
}

# simulator job titles -> LLD role categories (keyword fallback below)
ROLE_CATEGORY = {
    "HR Manager": "exec",
    "Tech Lead": "exec",
    "SRE": "exec",
    "Security Engineer": "exec",
    "HR Executive": "staff",
    "Accountant": "staff",
    "Finance Analyst": "staff",
    "Software Engineer": "staff",
    "DevOps Engineer": "staff",
    "SOC Analyst": "staff",
}

DEPT_FACTOR_IN_SCOPE = 1.0
DEPT_FACTOR_OUT_OF_SCOPE = 1.4

HOUR_RISK_IN_WINDOW = 0.2
HOUR_RISK_OUT_WINDOW = 0.8


def _get(obj, key: str, default=None):
    """Read `obj.key` or `obj[key]` (supports NormalizedEvent and plain dicts)."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


@dataclass
class ContextVector:
    who: str
    doing_what: str
    using_what: str
    from_where: str
    when: datetime
    hour_of_day_risk: float
    how_much: int
    target_sensitivity: float
    role_factor: float
    dept_factor: float
    baseline_confidence: float


def sensitivity_score(tier: str | None) -> float:
    """0-1 weight of a sensitivity tier (public … restricted)."""
    return SENSITIVITY_SCORE.get(tier or "", SENSITIVITY_SCORE["internal"])


def role_factor(role: str | None) -> float:
    """0-1 privilege weight of a role, keyword-mapped when not in the table."""
    if not role:
        return ROLE_FACTOR["staff"]
    category = ROLE_CATEGORY.get(role)
    if category is None:
        low = role.lower()
        if "admin" in low or "administrator" in low:
            category = "admin"
        elif "contractor" in low:
            category = "contractor"
        elif any(k in low for k in ("manager", "lead", "sre", "engineer")):
            category = "exec"
        else:
            category = "staff"
    return ROLE_FACTOR[category]


def confidence_weight(grade: str | None) -> float:
    """Baseline confidence grade → 0-1 weight (LOW judges gently)."""
    return CONFIDENCE_WEIGHT.get(grade or "LOW", CONFIDENCE_WEIGHT["LOW"])


def hour_of_day_risk(hour: int, active_window: dict | None) -> float:
    """0-1 risk of acting at `hour`: low inside the learned active window,
    higher outside (including windows that wrap past midnight)."""
    if not active_window:
        return HOUR_RISK_OUT_WINDOW
    start = active_window.get("start_hour", 0)
    end = active_window.get("end_hour", 23)
    inside = (start <= hour <= end) if start <= end else (hour >= start or hour <= end)
    return HOUR_RISK_IN_WINDOW if inside else HOUR_RISK_OUT_WINDOW


def build(ev, profile_selected, user, resource_owner: dict | None = None) -> ContextVector:
    """Context for one normalized event.

    `ev` — a `NormalizedEvent` (or schema dict). `profile_selected` — the
    baseline row chosen for this entity (needs `confidence` + `active_window`),
    or None for cold start. `user` — the acting Employee (`.role`,
    `.department`) or None. `resource_owner` — {resource: owning dept} used to
    decide the department-scope factor.
    """
    who = _get(ev, "entity_id", "")
    doing_what = _get(ev, "event_type", "")
    target = _get(ev, "target_entity")
    if target:
        doing_what = f"{doing_what} -> {target}"

    using_what = _get(ev, "source_entity") or _get(ev, "actor") or ""
    geo = _get(ev, "geo") or {}
    from_where = geo.get("city", "") if isinstance(geo, dict) else ""

    profile = profile_selected or {}
    when = _get(ev, "ts")

    # department scope factor (out-of-scope access raises impact)
    dept = getattr(user, "department", None) if user is not None else None
    owner = resource_owner.get(target) if (resource_owner and target) else None
    out_of_scope = bool(owner and dept and owner != dept)
    dept_factor = DEPT_FACTOR_OUT_OF_SCOPE if out_of_scope else DEPT_FACTOR_IN_SCOPE

    return ContextVector(
        who=who,
        doing_what=doing_what,
        using_what=using_what,
        from_where=from_where,
        when=when,
        hour_of_day_risk=hour_of_day_risk(when.hour, profile.get("active_window")),
        how_much=_get(ev, "bytes_moved", 0) or 0,
        target_sensitivity=sensitivity_score(_get(ev, "sensitivity")),
        role_factor=role_factor(getattr(user, "role", None) if user is not None else None),
        dept_factor=dept_factor,
        baseline_confidence=confidence_weight(profile.get("confidence")),
    )


__all__ = [
    "ContextVector",
    "SENSITIVITY_SCORE",
    "ROLE_FACTOR",
    "CONFIDENCE_WEIGHT",
    "ROLE_CATEGORY",
    "DEPT_FACTOR_IN_SCOPE",
    "DEPT_FACTOR_OUT_OF_SCOPE",
    "sensitivity_score",
    "role_factor",
    "confidence_weight",
    "hour_of_day_risk",
    "build",
]