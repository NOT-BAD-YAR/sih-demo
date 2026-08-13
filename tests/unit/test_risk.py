"""Phase 4D — Risk Engine unit tests.

Covers the 4.6 LLD contract: the multiplicative formula
`Risk = Anomaly × Impact × Confidence` (never additive), the capped rule
bonus (0.15), anomaly fusion (0.7 rules + 0.3 ML), impact blending, band
boundaries, input clamping, boundedness, reproducibility, and the
breakdown/"why flagged" payload.
"""

import pytest

from analytics.risk import (
    band_of,
    compute,
    fuse,
    impact,
    RULE_BONUS_CAP,
)

pytestmark = pytest.mark.unit


class TestComputeFormula:
    def test_risk_is_anomaly_times_impact_times_confidence(self):
        r = compute(anomaly=0.5, impact=0.8, confidence=0.7)
        # 0.5 * 0.8 * 0.7 = 0.28 → 28.0
        assert r.risk_100 == pytest.approx(28.0)

    def test_zero_any_component_zeroes_risk(self):
        assert compute(anomaly=1.0, impact=0.0, confidence=1.0).risk_100 == 0.0
        assert compute(anomaly=0.0, impact=0.8, confidence=1.0).risk_100 == 0.0

    def test_low_confidence_judges_gently(self):
        low = compute(anomaly=0.9, impact=0.9, confidence=0.4)  # sparse entity
        high = compute(anomaly=0.9, impact=0.9, confidence=1.0)  # rich entity
        assert low.risk_100 < high.risk_100
        assert low.risk_100 == pytest.approx(32.4)  # 0.9*0.9*0.4*100

    def test_full_signal_reaches_critical(self):
        r = compute(anomaly=1.0, impact=1.0, confidence=1.0)
        assert r.risk_100 == 100.0
        assert r.band == "Critical"


class TestRuleBonus:
    def test_bonus_adds_within_cap(self):
        base = compute(anomaly=0.1, impact=0.1, confidence=1.0)  # 1.0
        boosted = compute(anomaly=0.1, impact=0.1, confidence=1.0, rule_bonus=0.10)
        assert boosted.risk_100 == pytest.approx(11.0)  # 0.01 + 0.10

    def test_bonus_capped_at_0_15(self):
        r = compute(anomaly=0.0, impact=0.0, confidence=0.0, rule_bonus=1.0)
        assert r.risk_100 == pytest.approx(RULE_BONUS_CAP * 100.0)

    def test_negative_bonus_ignored(self):
        r = compute(anomaly=0.5, impact=0.5, confidence=0.5, rule_bonus=-1.0)
        assert r.risk_100 == pytest.approx(12.5)


class TestClampingAndBounds:
    def test_inputs_clamped_to_unit_interval(self):
        r = compute(anomaly=5.0, impact=5.0, confidence=5.0)
        assert r.risk_100 == 100.0
        assert r.anomaly == 1.0 and r.impact == 1.0 and r.confidence == 1.0

    def test_risk_never_exceeds_100(self):
        for a, i, c in [(1.0, 1.0, 1.0), (0.9, 0.9, 0.9), (0.2, 0.4, 0.6)]:
            assert 0.0 <= compute(a, i, c).risk_100 <= 100.0


class TestFuse:
    def test_rules_dominate_ml(self):
        assert fuse([0.8], ml_score=0.0) == pytest.approx(0.56)
        assert fuse([0.8, 0.6], ml_score=0.5) == pytest.approx(0.71)  # 0.7*0.8+0.3*0.5

    def test_no_rules_uses_only_ml(self):
        assert fuse([], ml_score=0.5) == pytest.approx(0.15)
        assert fuse([], ml_score=0.0) == 0.0

    def test_takes_max_rule_severity(self):
        assert fuse([0.2, 0.9, 0.4], ml_score=0.0) == pytest.approx(0.63)


class TestImpact:
    def test_max_of_sensitivity_and_role(self):
        assert impact(target_sensitivity=0.6, role_factor=0.9) == pytest.approx(0.9)
        assert impact(target_sensitivity=0.9, role_factor=0.6) == pytest.approx(0.9)

    def test_department_scope_blend(self):
        assert impact(0.6, 0.6, dept_factor=1.4) == pytest.approx(0.84)
        assert impact(0.6, 0.9, dept_factor=1.4) == pytest.approx(1.0)  # capped

    def test_bounded(self):
        assert 0.0 <= impact(0.9, 0.9, dept_factor=1.4) <= 1.0


class TestBandOf:
    def test_boundaries(self):
        assert band_of(0) == "Low"
        assert band_of(24.9) == "Low"
        assert band_of(25) == "Medium"
        assert band_of(49.9) == "Medium"
        assert band_of(50) == "High"
        assert band_of(74.9) == "High"
        assert band_of(75) == "Critical"
        assert band_of(100) == "Critical"

    def test_custom_thresholds(self):
        assert band_of(60, band_high=50, band_critical=75) == "High"
        assert band_of(60, band_high=70, band_critical=90) == "Medium"


class TestReproducibility:
    def test_same_inputs_same_result(self):
        args = dict(anomaly=0.65, impact=0.7, confidence=0.7, rule_bonus=0.05,
                    components={"rules": [0.8], "ml": 0.5})
        a = compute(**args)
        b = compute(**args)
        assert a.risk_100 == b.risk_100
        assert a.breakdown == b.breakdown

    def test_breakdown_shape(self):
        r = compute(anomaly=0.5, impact=0.5, confidence=0.7,
                    components={"rules": [0.8], "ml": 0.5})
        b = r.breakdown
        assert set(b) == {"risk", "band", "anomaly", "impact", "confidence", "components"}
        assert b["components"] == {"rules": [0.8], "ml": 0.5}