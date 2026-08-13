"""Risk Engine — 4.6 Risk = Anomaly × Impact × Confidence (0-100 + bands).

The foundation is multiplicative, never additive-capped. `anomaly` fuses the
deterministic rule severities with the ML signal, `impact` blends target
sensitivity / role privilege with department scope, and `confidence` is the
baseline weight so new/sparse entities are judged gently (LOW → 0.4).

Every result carries a `breakdown` dict — the dashboard's "why flagged"
payload: `{risk, band, anomaly, impact, confidence, components:{rules, ml}}`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .config import Config

RULE_BONUS_CAP = 0.15
FUSE_RULE_WEIGHT = 0.7
FUSE_ML_WEIGHT = 0.3

BAND_LOW_MAX = 25  # 0-24 Low, 25+ Medium … (25 is the floor of Medium)


@dataclass
class Risk:
    risk_100: float
    band: str
    anomaly: float
    impact: float
    confidence: float
    components: dict = field(default_factory=dict)

    @property
    def breakdown(self) -> dict:
        """Serializable "why flagged" payload for the dashboard."""
        return {
            "risk": self.risk_100,
            "band": self.band,
            "anomaly": self.anomaly,
            "impact": self.impact,
            "confidence": self.confidence,
            "components": dict(self.components),
        }


def fuse(rule_severities, ml_score: float = 0.0) -> float:
    """Anomaly 0-1: 0.7 × max(rule severity) + 0.3 × ML signal."""
    max_rule = max(rule_severities) if rule_severities else 0.0
    return round(FUSE_RULE_WEIGHT * max_rule + FUSE_ML_WEIGHT * ml_score, 6)


def impact(target_sensitivity: float, role_factor: float, dept_factor: float = 1.0) -> float:
    """Impact 0-1: max(sensitivity, role) blended with department scope."""
    return round(min(1.0, max(target_sensitivity, role_factor) * dept_factor), 6)


def band_of(score: float, *, band_high: int | None = None, band_critical: int | None = None) -> str:
    """0-100 → Low/Medium/High/Critical (thresholds from Config by default)."""
    cfg = Config.from_env()
    high = band_high if band_high is not None else cfg.risk_band_high
    critical = band_critical if band_critical is not None else cfg.risk_band_critical
    if score < BAND_LOW_MAX:
        return "Low"
    if score < high:
        return "Medium"
    if score < critical:
        return "High"
    return "Critical"


def compute(
    anomaly: float,
    impact: float,
    confidence: float,
    rule_bonus: float = 0.0,
    *,
    components: dict | None = None,
) -> Risk:
    """Risk = Anomaly × Impact × Confidence → 0-100, plus a small capped
    rule bonus, mapped to a band. All inputs clamped to 0-1."""
    anomaly = max(0.0, min(1.0, anomaly))
    impact = max(0.0, min(1.0, impact))
    confidence = max(0.0, min(1.0, confidence))

    risk_01 = anomaly * impact * confidence
    bonus = min(max(rule_bonus, 0.0), RULE_BONUS_CAP)
    risk_100 = round(min(1.0, risk_01 + bonus) * 100.0, 2)

    return Risk(
        risk_100=risk_100,
        band=band_of(risk_100),
        anomaly=anomaly,
        impact=impact,
        confidence=confidence,
        components=components or {},
    )


__all__ = [
    "Risk",
    "RULE_BONUS_CAP",
    "FUSE_RULE_WEIGHT",
    "FUSE_ML_WEIGHT",
    "fuse",
    "impact",
    "band_of",
    "compute",
]