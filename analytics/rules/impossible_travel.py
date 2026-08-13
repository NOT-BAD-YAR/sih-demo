"""Impossible-travel rule  -  canonical anomaly #2.

Triggers when two logins for the same user occur from geo locations that are
farther apart than physical travel allows in the elapsed time. Deterministic  - 
uses the haversine geodistance and a speed threshold (default 600 km/h), no ML.
"""

from __future__ import annotations

from datetime import datetime

from . import RuleResult, clamp01, not_triggered

RULE_NAME = "impossible_travel"

DEFAULT_SPEED_KMH = 600.0


def _distance_km(geo_a: dict, geo_b: dict) -> float:
    from analytics.features import haversine_km

    return haversine_km(
        float(geo_a.get("lat", 0.0)),
        float(geo_a.get("lon", 0.0)),
        float(geo_b.get("lat", 0.0)),
        float(geo_b.get("lon", 0.0)),
    )


def evaluate(
    login_pairs: list[tuple[dict, datetime]],
    speed_threshold_kmh: float = DEFAULT_SPEED_KMH,
    evidence: list[str] | None = None,
) -> RuleResult:
    """Detect impossible travel across consecutive login geo/ts pairs.

    `login_pairs` is an ordered list of `(geo_dict, ts)` for one entity.
    """
    evidence = evidence or []
    if len(login_pairs) < 2:
        return not_triggered(RULE_NAME)

    best: tuple[float, float, str, str, float] | None = None  # (severity, dist_km, city_a, city_b, dt_hours)
    for (geo_a, ts_a), (geo_b, ts_b) in zip(login_pairs, login_pairs[1:]):
        if ts_b <= ts_a:
            continue
        distance = _distance_km(geo_a, geo_b)
        dt_hours = (ts_b - ts_a).total_seconds() / 3600.0
        if dt_hours <= 0:
            continue
        speed = distance / dt_hours
        if speed > speed_threshold_kmh:
            severity = clamp01((speed - speed_threshold_kmh) / (speed_threshold_kmh * 4))
            if best is None or severity > best[0]:
                best = (severity, distance, geo_a.get("city", "?"), geo_b.get("city", "?"), dt_hours)

    if best is None:
        return not_triggered(RULE_NAME)

    severity, distance, city_a, city_b, dt_hours = best
    explanation = (
        f"Impossible travel: {city_a} to {city_b} ({distance:.0f} km apart) in "
        f"{dt_hours * 60:.0f} minutes implies {distance / dt_hours:.0f} km/h, "
        f"above the {speed_threshold_kmh:.0f} km/h physical limit"
    )
    return RuleResult(RULE_NAME, True, severity, explanation, evidence)