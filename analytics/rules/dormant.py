"""Dormant-account activation rule  -  canonical anomaly #4.

Triggers when an account that has been idle for more than `dormant_days`
(30) becomes active again, and the activity happens outside the entity's
learned `active_window` (i.e. during cold hours). The dormant duration and the
cold hour are both cited in the explanation.
"""

from __future__ import annotations

from datetime import datetime

from . import RuleResult, clamp01, not_triggered

RULE_NAME = "dormant"

DEFAULT_DORMANT_DAYS = 30
SEVERITY = 0.6


def _hour_in_window(hour: int, active_window: dict | None) -> bool:
    if not active_window:
        return True  # unknown window  to  do not judge (cold start)
    start = int(active_window.get("start_hour", 0))
    end = int(active_window.get("end_hour", 23))
    if start <= end:
        return start <= hour <= end
    # window wraps midnight, e.g. 22:00  to  04:00
    return hour >= start or hour <= end


def evaluate(
    ev: dict,
    active_window: dict | None,
    staleness_days: float,
    dormant_days: float = DEFAULT_DORMANT_DAYS,
    evidence: list[str] | None = None,
) -> RuleResult:
    """Detect a dormant account becoming active in a cold hour."""
    evidence = evidence or []
    ts = ev.get("ts")
    hour = ts.hour if isinstance(ts, datetime) else 0

    if staleness_days <= dormant_days:
        return not_triggered(RULE_NAME)
    if _hour_in_window(hour, active_window):
        return not_triggered(RULE_NAME)

    explanation = (
        f"Dormant account {ev.get('entity_id', '?')} activated after "
        f"{staleness_days:.0f} days idle (threshold {dormant_days:.0f}), at "
        f"{hour:02d}:00  -  outside the learned active window "
        f"{_fmt_window(active_window)}"
    )
    severity = clamp01(SEVERITY + (staleness_days - dormant_days) / (dormant_days * 4))
    return RuleResult(RULE_NAME, True, severity, explanation, evidence)


def _fmt_window(active_window: dict | None) -> str:
    if not active_window:
        return "unknown"
    return f"{int(active_window.get('start_hour', 0)):02d}:00 - {int(active_window.get('end_hour', 23)):02d}:00"