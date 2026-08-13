"""Risk drill-down view (Phase 6) — real data, no org/ML in the API process.

`GET /users/{id}/risk` and `GET /entities/{id}/risk` return:
    {current, history: [{ts, risk, band, explanation}], explanation,
     baseline_snapshot}

The view is computed from what the engine already persisted:
  * `history` — one entry per stored feature window. The anomaly is the
    volume-spike rule severity (pure function over the stored window + the
    stored individual baseline) OR a z-score deviation of any other baseline
    feature, whichever is stronger. Impact comes from the window's own
    sensitivity histogram; confidence from the stored baseline grade.
  * `current` — the max risk among the entity's OPEN alerts/incidents, else
    the most recent window risk (kept in lock-step with the engine's own
    stored read models).
  * `baseline_snapshot` — the stored individual `feature_stats`.

ML is deliberately 0.0 here: Isolation-Forest models live in the engine
process, not the API process (documented limitation, not a silent gap).
"""

from __future__ import annotations

from analytics.lifecycle import TERMINAL_STATES
from analytics.risk import compute, fuse, band_of
from analytics.rules import clamp01, run_rule

_CONFIDENCE_WEIGHT = {"HIGH": 1.0, "MED": 0.7, "LOW": 0.4}
_DEVIATION_FEATURES = (
    "volume",
    "event_count",
    "active_hours_frac",
    "location_count",
    "location_dist_km",
    "new_peer_count",
    "fail_rate",
)
_SENSITIVITY_TIERS = {"public": 0.3, "internal": 0.5, "confidential": 0.75, "restricted": 1.0}


def _deviation_severity(value: float, stats: dict, k: float = 3.0) -> float:
    """z-score deviation beyond k standard deviations, clipped to 0-1."""
    mean, std = stats.get("mean"), stats.get("std")
    if mean is None or std is None or not std:
        return 0.0
    z = abs(float(value) - float(mean)) / float(std)
    if z <= k:
        return 0.0
    return clamp01((z - k) / k)


def _window_anomaly(window: dict, profile: dict | None) -> tuple[float, str]:
    """Anomaly 0-1 + explanation for one stored feature window."""
    result = run_rule("volume_spike", window=window, profile_individual=profile)
    severity = float(result.severity)
    explanation = result.explanation
    stats = ((profile or {}).get("feature_stats") or {})
    for feature in _DEVIATION_FEATURES:
        if feature == "volume":
            continue  # volume_spike rule already judges it
        sev = _deviation_severity(window.get(feature, 0.0) or 0.0, stats.get(feature) or {})
        if sev > severity:
            severity = sev
            mean = (stats.get(feature) or {}).get("mean", 0.0)
            explanation = (
                f"{feature} {window.get(feature, 0.0) or 0.0:.2f} deviates "
                f"{abs((window.get(feature, 0.0) or 0.0) - mean):.2f} from baseline mean {mean:.2f}"
            )
    return severity, explanation


def _window_impact(window: dict) -> float:
    shist = window.get("sensitivity_hist") or {}
    return max((_SENSITIVITY_TIERS.get(tier, 0.5) for tier in shist), default=0.5)


def _confidence(profile: dict | None) -> float:
    return _CONFIDENCE_WEIGHT.get((profile or {}).get("confidence"), 0.4)


def entity_risk_view(conn, entity_ref: str) -> dict:
    """Compose the drill-down payload for one entity (user or device/server)."""
    from db.dao import get_alerts, get_incidents, get_profile, get_windows

    windows = get_windows(conn, entity_ref)
    profile = get_profile(conn, entity_ref, "individual")

    history: list[dict] = []
    for w in windows:
        anomaly, explanation = _window_anomaly(w, profile)
        risk = compute(
            fuse([anomaly]),
            _window_impact(w),
            _confidence(profile),
            components={"rules": [anomaly], "ml": 0.0, "explanation": explanation},
        )
        history.append({"ts": w["window_start"], "risk": risk.risk_100, "band": risk.band, "explanation": explanation})

    rows = [r for r in get_incidents(conn) + get_alerts(conn) if (r.get("entity_ref") or "").lower() == str(entity_ref).lower()]
    open_risk = [float(r["risk"]) for r in rows if r["status"] not in TERMINAL_STATES and r.get("risk") is not None]

    current = max(open_risk) if open_risk else (history[-1]["risk"] if history else 0.0)
    explanation = history[-1]["explanation"] if history else "no stored windows yet"
    return {
        "entity_ref": entity_ref,
        "current": {"risk": round(current, 2), "band": band_of(current)},
        "history": history,
        "explanation": explanation,
        "baseline_snapshot": (profile or {}).get("feature_stats"),
    }


__all__ = ["entity_risk_view"]