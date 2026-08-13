"""Phase 6 — risk drill-down view unit tests (pure helpers, no DB).

The DB-bound `entity_risk_view` is exercised in the Phase 6 integration suite;
here we pin the pure building blocks:
  * z-score deviation beyond k std deviations is clipped 0-1 and zero inside;
  * the volume-spike rule severity beats plain deviation when it fires;
  * impact derives from the window's own sensitivity histogram;
  * confidence maps the stored baseline grade (HIGH/MED/LOW → 1.0/0.7/0.4).
"""

from analytics.rules import clamp01
from api.risk_view import _confidence, _deviation_severity, _window_anomaly, _window_impact


class TestDeviationSeverity:
    def test_zero_inside_three_sigma(self):
        assert _deviation_severity(10.0, {"mean": 10.0, "std": 2.0}) == 0.0

    def test_ramps_beyond_three_sigma(self):
        sev = _deviation_severity(20.0, {"mean": 10.0, "std": 2.0})  # z = 5
        assert 0.0 < sev <= 1.0

    def test_clipped_to_one(self):
        sev = _deviation_severity(1_000_000.0, {"mean": 10.0, "std": 1.0})
        assert sev == clamp01(1.0)

    def test_missing_stats_are_safe(self):
        assert _deviation_severity(99.0, {}) == 0.0
        assert _deviation_severity(99.0, {"mean": 10.0, "std": 0}) == 0.0


class TestWindowAnomaly:
    SPIKE = {
        "volume": 500_000_000, "event_count": 12, "active_hours_frac": 0.5,
        "location_count": 1, "location_dist_km": 0.0, "new_peer_count": 0,
        "fail_rate": 0.0, "sensitivity_hist": {"internal": 12},
    }

    def test_volume_spike_rule_fires_against_baseline(self):
        profile = {"feature_stats": {"volume": {"mean": 10_000_000.0, "std": 1_000_000.0}},
                   "confidence": "HIGH"}
        anomaly, explanation = _window_anomaly(self.SPIKE, profile)
        assert anomaly > 0.0
        assert "Volume" in explanation

    def test_volume_ratio_matches_rule(self):
        profile = {"feature_stats": {"volume": {"mean": 10_000_000.0, "std": 1_000_000.0}},
                   "confidence": "HIGH"}
        anomaly, _ = _window_anomaly(self.SPIKE, profile)
        from analytics.rules import run_rule
        expected = run_rule("volume_spike", window=self.SPIKE, profile_individual=profile)
        assert anomaly == expected.severity

    def test_no_baseline_is_gentle(self):
        anomaly, _ = _window_anomaly(self.SPIKE, None)
        assert 0.0 <= anomaly <= 1.0


class TestImpactAndConfidence:
    def test_impact_from_sensitivity_hist(self):
        assert _window_impact({"sensitivity_hist": {"internal": 12}}) == 0.5
        assert _window_impact({"sensitivity_hist": {"restricted": 4}}) == 1.0
        assert _window_impact({"sensitivity_hist": {}}) == 0.5

    def test_confidence_mapping(self):
        assert _confidence({"confidence": "HIGH"}) == 1.0
        assert _confidence({"confidence": "MED"}) == 0.7
        assert _confidence({"confidence": "LOW"}) == 0.4
        assert _confidence(None) == 0.4