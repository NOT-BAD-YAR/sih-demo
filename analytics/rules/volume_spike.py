"""Volume-spike rule  -  canonical anomaly #1.

Triggers when an hour window's `volume` exceeds the individual baseline mean
by more than `K`x (default K=5). When the individual baseline is weak, the
peer-group baseline applies with a gentler threshold and lower severity.
"""

from __future__ import annotations

from . import RuleResult, clamp01, not_triggered

RULE_NAME = "volume_spike"

DEFAULT_K = 5.0          # individual threshold: volume > K x mean
PEER_GROUP_K = 1.5       # peer-group threshold: volume > 1.5 x group mean
PEER_GROUP_WEAK = 0.5    # influence weight when only the peer group fires


def _fmt_bytes(value: float) -> str:
    """Human-readable byte size: 12 KB vs 4.5 GB  -  avoids '0 MB' artifacts."""
    value = float(value)
    for unit, div in (("GB", 1024 ** 3), ("MB", 1024 ** 2), ("KB", 1024)):
        if value >= div:
            return f"{value / div:.1f} {unit}"
    return f"{value:.0f} B"


def _mean_of(profile: dict | None, feature: str = "volume") -> float:
    if not profile:
        return 0.0
    stats = (profile.get("feature_stats") or {}).get(feature)
    if not stats:
        return 0.0
    return float(stats.get("mean", 0.0))


def evaluate(
    window: dict,
    profile_individual: dict | None = None,
    profile_peer_group: dict | None = None,
    k: float = DEFAULT_K,
    evidence: list[str] | None = None,
) -> RuleResult:
    """Detect a volume spike against the individual/peer-group baselines."""
    volume = float(window.get("volume", 0) or 0)
    ind_mean = _mean_of(profile_individual)
    peer_mean = _mean_of(profile_peer_group)

    evidence = evidence or []

    # Individual baseline is the primary judge.
    if ind_mean > 0:
        ratio = volume / ind_mean
        if ratio > k:
            explanation = (
                f"Volume {_fmt_bytes(volume)} is {ratio:.1f}x the individual "
                f"baseline mean of {_fmt_bytes(ind_mean)}/hour"
            )
            severity = clamp01((ratio - k) / (k * 4)) * 1.0
            return RuleResult(RULE_NAME, True, severity, explanation, evidence)

    # Weak individual  to  peer-group baseline with a gentler threshold.
    if peer_mean > 0:
        ratio = volume / peer_mean
        if ratio > PEER_GROUP_K:
            explanation = (
                f"Volume {_fmt_bytes(volume)} is {ratio:.1f}x the peer-group "
                f"baseline mean of {_fmt_bytes(peer_mean)}/hour"
            )
            severity = clamp01((ratio - PEER_GROUP_K) / (PEER_GROUP_K * 4)) * PEER_GROUP_WEAK
            return RuleResult(RULE_NAME, True, severity, explanation, evidence)

    return not_triggered(RULE_NAME)