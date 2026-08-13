"""Feature Engine — 4.1 windowed feature construction.

Builds hour-sized `FeatureWindow`s per entity (keyed on `(entity_ref, hour)`).
`accumulate` folds each normalized event into the running window (incremental),
`finalize` emits the JSONB-serializable vector stored in `feature_windows` and
used by the baseline engine + rule detectors.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .processor import NormalizedEvent

HOUR_SECONDS = 3600.0


def hour_bucket(ts: datetime) -> datetime:
    """Floor a timestamp to its hour boundary (timezone-aware)."""
    return ts.replace(minute=0, second=0, microsecond=0)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres between two geo points."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


@dataclass
class FeatureWindow:
    """Aggregated behaviour of one entity inside one hour bucket.

    All fields are finalised features; internal bookkeeping lives in the
    underscore-prefixed attributes and is NOT part of the stored vector.
    """

    entity_ref: str
    window_start: datetime  # hour boundary (UTC)

    # --- final features ---
    volume: int = 0
    event_count: int = 0
    active_hours_frac: float = 0.0
    unique_peers: set[str] = field(default_factory=set)
    new_peer_count: int = 0
    location_count: int = 0
    location_dist_km: float = 0.0
    dept_distinct: set[str] = field(default_factory=set)
    sensitivity_hist: dict[str, int] = field(default_factory=dict)
    fail_rate: float = 0.0
    staleness_days: int = 0

    # --- internal accumulation state (excluded from the vector) ---
    _first_ts: datetime | None = None
    _last_ts: datetime | None = None
    _fail_count: int = 0
    _locations: set[str] = field(default_factory=set)
    _geo_samples: list[tuple[float, float]] = field(default_factory=list)

    def dept_from_path(self, path: str | None) -> str | None:
        """Department name from a simulator-style `/Dept/resource/file` path."""
        if not path:
            return None
        parts = [p for p in path.strip("/").split("/") if p]
        return parts[0] if parts else None


def _new_window(entity_ref: str, ts: datetime) -> FeatureWindow:
    return FeatureWindow(entity_ref=entity_ref, window_start=hour_bucket(ts))


def accumulate(
    existing: FeatureWindow | None,
    ev: NormalizedEvent,
) -> FeatureWindow:
    """Fold one event into the running window for its entity/hour bucket.

    Creates a fresh window when `existing` is None. The caller is responsible
    for fetching the correct window per `(entity_ref, hour_bucket(ev.ts))`.
    """
    w = existing if existing is not None else _new_window(ev.entity_id, ev.ts)

    w.volume += ev.bytes_moved
    w.event_count += 1
    if ev.peer_entity:
        w.unique_peers.add(ev.peer_entity)
    if ev.geo and ev.geo.get("city"):
        w._locations.add(ev.geo["city"])
    if ev.geo and ev.geo.get("lat") is not None and ev.geo.get("lon") is not None:
        w._geo_samples.append((float(ev.geo["lat"]), float(ev.geo["lon"])))
    if ev.sensitivity:
        w.sensitivity_hist[ev.sensitivity] = w.sensitivity_hist.get(ev.sensitivity, 0) + 1
    if ev.outcome == "failure":
        w._fail_count += 1

    dept = w.dept_from_path(ev.file_path)
    if dept:
        w.dept_distinct.add(dept)

    if w._first_ts is None or ev.ts < w._first_ts:
        w._first_ts = ev.ts
    if w._last_ts is None or ev.ts > w._last_ts:
        w._last_ts = ev.ts

    return w


def _max_pairwise_distance(samples: list[tuple[float, float]]) -> float:
    if len(samples) < 2:
        return 0.0
    best = 0.0
    for i in range(len(samples)):
        for j in range(i + 1, len(samples)):
            d = haversine_km(*samples[i], *samples[j])
            if d > best:
                best = d
    return best


def finalize(w: FeatureWindow) -> dict:
    """Compute the closed-bucket feature vector (JSONB-serializable)."""
    span_seconds = 0.0
    if w._first_ts is not None and w._last_ts is not None:
        span_seconds = (w._last_ts - w._first_ts).total_seconds()
    active_frac = min(1.0, span_seconds / HOUR_SECONDS)

    fail_rate = (w._fail_count / w.event_count) if w.event_count else 0.0

    w.active_hours_frac = active_frac
    w.location_count = len(w._locations)
    w.location_dist_km = _max_pairwise_distance(w._geo_samples)
    w.fail_rate = round(fail_rate, 6)
    w.new_peer_count = 0  # computed against baseline known set in Phase 4B

    return {
        "entity_ref": w.entity_ref,
        "window_start": w.window_start.isoformat(),
        "volume": w.volume,
        "event_count": w.event_count,
        "active_hours_frac": round(w.active_hours_frac, 6),
        "unique_peers": sorted(w.unique_peers),
        "new_peer_count": w.new_peer_count,
        "location_count": w.location_count,
        "location_dist_km": round(w.location_dist_km, 6),
        "dept_distinct": sorted(w.dept_distinct),
        "sensitivity_hist": dict(sorted(w.sensitivity_hist.items())),
        "fail_rate": w.fail_rate,
        "staleness_days": w.staleness_days,
    }


def accumulate_all(events: list[NormalizedEvent]) -> dict[str, FeatureWindow]:
    """Fold a list of events into per-entity windows keyed by `(entity_ref, hour)`.

    Convenience for backfill / tests: returns {f"{entity_ref}@{hour}": window}.
    """
    windows: dict[str, FeatureWindow] = {}
    for ev in events:
        key = f"{ev.entity_id}@{hour_bucket(ev.ts).isoformat()}"
        existing = windows.get(key)
        windows[key] = accumulate(existing, ev)
    return windows


def staleness_before(w: FeatureWindow, last_activity: datetime | None) -> int:
    """Days since `last_activity` before the window started (dormant rule).

    Returns 0 when unknown (cold start) — dormant detection needs a real
    gap, so a missing history is treated gently.
    """
    if last_activity is None:
        return 0
    window_day = hour_bucket(w.window_start).date()
    last_day = last_activity.date() if isinstance(last_activity, datetime) else last_activity
    days = (window_day - last_day).days
    return max(0, days)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def window_ts(w: FeatureWindow) -> datetime:
    """Reconstruct the window start as a timezone-aware datetime."""
    return w.window_start if w.window_start.tzinfo else w.window_start.replace(tzinfo=timezone.utc)
