"""Baseline Engine — 4.2 three-level baselines + confidence + cold start.

Builds the behavioural baseline at three levels — individual, peer-group,
global — from closed feature windows. Every stat carries a sample count and a
confidence grade so the risk engine can weight LOW-confidence comparisons
gently. Cold-start: entities with sparse individual history fall back to the
peer-group baseline, then the global baseline.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from statistics import mean, pstdev, fmean
from typing import Iterable

# --- confidence thresholds --------------------------------------------------
CONFIDENCE_LOW_MAX = 20      # counts <  20  → LOW
CONFIDENCE_MED_MAX = 100     # counts <= 100 → MED, > 100 → HIGH
COLD_START_MIN_COUNT = 20    # individual baselines need >= 20 windows to judge
PEER_GROUP_MIN_COUNT = 5     # peer-group baselines need >= 5 members to judge

# numeric per-window features that get mean/std/count statistics
NUMERIC_FEATURES = (
    "volume",
    "event_count",
    "active_hours_frac",
    "location_count",
    "location_dist_km",
    "fail_rate",
    "staleness_days",
)

# set-valued features → allowed_sets keys
ALLOWED_SETS_MAP = {
    "unique_peers": "peers",
    "dept_distinct": "dept_paths",
}

ALLOWED_SET_KEYS = ("locations", "peers", "dept_paths", "sensitivity")


@dataclass(frozen=True)
class BaselineStats:
    mean: float
    std: float
    count: int
    confidence: str


def confidence_for(count: int) -> str:
    """Confidence grade from a sample count: <20 LOW · 20–100 MED · >100 HIGH."""
    if count < CONFIDENCE_LOW_MAX:
        return "LOW"
    if count <= CONFIDENCE_MED_MAX:
        return "MED"
    return "HIGH"


def _stats(values: Iterable[float]) -> BaselineStats:
    vals = [float(v) for v in values]
    n = len(vals)
    if n == 0:
        return BaselineStats(0.0, 0.0, 0, "LOW")
    if n == 1:
        return BaselineStats(vals[0], 0.0, 1, confidence_for(1))
    return BaselineStats(
        mean=fmean(vals),
        std=pstdev(vals),
        count=n,
        confidence=confidence_for(n),
    )


def _window_features(window: dict) -> dict:
    """Finalized window vector → dict of numeric features + set features."""
    numeric = {f: window.get(f, 0.0) for f in NUMERIC_FEATURES}
    sets = {f: set(window.get(f, []) or []) for f in ALLOWED_SETS_MAP}
    return {"numeric": numeric, "sets": sets}


def _window_hour(window: dict) -> int:
    from .features import hour_bucket
    from .processor import NormalizedEvent  # noqa: F401  (type only)

    raw = window.get("window_start")
    if isinstance(raw, str):
        raw = datetime.fromisoformat(raw)
    return hour_bucket(raw).hour


def _active_window(windows: list[dict]) -> dict:
    """{start_hour, end_hour} from the hour-of-day distribution (5th/95th pct)."""
    hours = sorted(_window_hour(w) for w in windows)
    if not hours:
        return {"start_hour": 0, "end_hour": 0}
    if len(hours) == 1:
        return {"start_hour": hours[0], "end_hour": hours[0]}
    # rank-based 5th/95th percentile to tolerate one-off early/late activity
    lo = hours[min(len(hours) - 1, max(0, round(0.05 * len(hours)) - 1))]
    hi = hours[max(0, round(0.95 * len(hours)) - 1)]
    return {"start_hour": lo, "end_hour": max(lo, hi)}


def _collect_sets(windows: list[dict]) -> dict:
    """Union of set-features across windows → allowed_sets shape."""
    collected: dict[str, set] = {
        "locations": set(),
        "peers": set(),
        "dept_paths": set(),
        "sensitivity": set(),
    }
    for w in windows:
        feats = _window_features(w)
        for src, dest in ALLOWED_SETS_MAP.items():
            collected[dest] |= feats["sets"].get(src, set())
        hist = w.get("sensitivity_hist") or {}
        collected["sensitivity"] |= set(hist.keys())
        for city in (w.get("locations") or []):
            collected["locations"].add(city)
    return collected


def build_individual(entity_ref: str, windows: list[dict]) -> dict:
    """Build an individual behavioural_profiles row from closed windows."""
    numeric_all = {f: [] for f in NUMERIC_FEATURES}
    for w in windows:
        feats = _window_features(w)
        for f in NUMERIC_FEATURES:
            numeric_all[f].append(feats["numeric"].get(f, 0.0))

    stats = {f: _stats(numeric_all[f]).__dict__ for f in NUMERIC_FEATURES}
    total_count = len(windows)
    allowed = _collect_sets(windows)
    return {
        "entity_ref": entity_ref,
        "level": "individual",
        "feature_stats": stats,
        "allowed_sets": {k: sorted(v) for k, v in allowed.items()},
        "active_window": _active_window(windows),
        "confidence": confidence_for(total_count),
        "updated_to": _now_iso(),
        "_count": total_count,
    }


def _aggregate_profiles(rows: list[dict]) -> dict:
    """Merge member profile rows into a parent (peer-group/global) row.

    Numeric stats combine with count-weighted means and pooled variance;
    allowed_sets and active_window are unions across members.
    """
    merged_sets: dict[str, set] = {k: set() for k in ALLOWED_SET_KEYS}
    starts: set[int] = set()
    ends: set[int] = set()
    for r in rows:
        for k, v in (r.get("allowed_sets") or {}).items():
            merged_sets[k] |= set(v)
        active = r.get("active_window") or {}
        if active.get("start_hour") is not None:
            starts.add(int(active["start_hour"]))
        if active.get("end_hour") is not None:
            ends.add(int(active["end_hour"]))

    feature_stats: dict = {}
    for f in NUMERIC_FEATURES:
        parts = []
        for r in rows:
            s = (r.get("feature_stats") or {}).get(f) or {}
            parts.append((float(s.get("mean", 0.0)), float(s.get("std", 0.0)), int(s.get("count", 0))))
        total = sum(p[2] for p in parts)
        if total == 0:
            feature_stats[f] = BaselineStats(0.0, 0.0, 0, "LOW").__dict__
            continue
        w_mean = sum(p[0] * p[2] for p in parts) / total
        pooled_var = sum(p[2] * (p[1] ** 2) for p in parts) / total
        feature_stats[f] = {
            "mean": round(w_mean, 6),
            "std": round(math.sqrt(pooled_var), 6),
            "count": total,
            "confidence": confidence_for(total),
        }

    total_count = sum(r.get("_count", 0) for r in rows)
    return {
        "feature_stats": feature_stats,
        "allowed_sets": {k: sorted(v) for k, v in merged_sets.items()},
        "active_window": {
            "start_hour": min(starts) if starts else 0,
            "end_hour": max(ends) if ends else 0,
        },
        "confidence": confidence_for(total_count),
        "updated_to": _now_iso(),
        "_count": total_count,
    }


def build_peer_group(peer_group_id: str, member_profiles: list[dict]) -> dict:
    """Aggregate member individual profiles into a peer-group row."""
    merged = _aggregate_profiles(member_profiles)
    return {
        "entity_ref": peer_group_id,
        "level": "peer_group",
        **merged,
    }


def build_global(all_profiles: list[dict]) -> dict:
    """Aggregate every individual profile into the org-wide global row."""
    merged = _aggregate_profiles(all_profiles)
    return {
        "entity_ref": "__global__",
        "level": "global",
        **merged,
    }


def select_level(
    entity_ref: str,
    profiles: dict[str, dict | None],
) -> tuple[str, dict]:
    """Cold-start baseline selection for scoring.

    Returns (level, profile_row). Rules:
      1. individual profile with count >= 20       → individual
      2. else peer-group profile with count >= 5   → peer_group
      3. else global profile                       → global
    Raises ValueError when no usable baseline exists (all None) — callers
    treat that as "no signal yet" and score neutrally.
    """
    individual = profiles.get("individual")
    if individual and individual.get("_count", 0) >= COLD_START_MIN_COUNT:
        return "individual", individual

    peer = profiles.get("peer_group")
    if peer and peer.get("_count", 0) >= PEER_GROUP_MIN_COUNT:
        return "peer_group", peer

    global_row = profiles.get("global")
    if global_row is not None:
        return "global", global_row

    raise ValueError(f"no baseline available for {entity_ref!r}")


def rolling_retrain(entity_ref: str, windows: list[dict], last_n_days: int = 30) -> dict:
    """Daily job: rebuild an individual baseline from the last N days of windows.

    Keeps drift-following. The DB round-trip (read windows → build → upsert
    profile) is orchestrated by the runner (4.8); this pure function does the
    rebuild given the already-filtered recent windows.
    """
    recent = []
    cutoff = datetime.now(timezone.utc) - timedelta(days=last_n_days)
    for w in windows:
        raw = w.get("window_start")
        ts = datetime.fromisoformat(raw) if isinstance(raw, str) else raw
        if ts and ts >= cutoff:
            recent.append(w)
    if not recent:
        recent = windows
    return build_individual(entity_ref, recent)


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


__all__ = [
    "BaselineStats",
    "confidence_for",
    "build_individual",
    "build_peer_group",
    "build_global",
    "select_level",
    "rolling_retrain",
    "NUMERIC_FEATURES",
    "COLD_START_MIN_COUNT",
    "PEER_GROUP_MIN_COUNT",
]
