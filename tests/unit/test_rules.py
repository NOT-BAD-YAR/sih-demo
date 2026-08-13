"""Phase 4B — Rule detectors (5 canonical cases) tests.

Each rule is table-driven: normal cases must not trigger, anomalous cases
must trigger with an explainable sentence and a 0–1 severity.
"""

import pytest
from datetime import datetime, timezone, timedelta

from analytics.rules import RuleResult, run_rule, rule_names, not_triggered
from analytics.rules.volume_spike import evaluate as vol_eval
from analytics.rules.impossible_travel import evaluate as travel_eval
from analytics.rules.out_of_scope import evaluate as scope_eval
from analytics.rules.dormant import evaluate as dormant_eval
from analytics.rules.novel_peer import evaluate as peer_eval

pytestmark = pytest.mark.unit

NOW = datetime(2026, 1, 15, 10, 30, tzinfo=timezone.utc)

GEO = {"Chennai": {"city": "Chennai", "lat": 13.08, "lon": 80.27},
       "Delhi": {"city": "Delhi", "lat": 28.61, "lon": 77.21}}


def _profile(mean_bytes: float) -> dict:
    return {
        "feature_stats": {"volume": {"mean": mean_bytes, "std": 0.0, "count": 60, "confidence": "HIGH"}},
        "allowed_sets": {},
        "active_window": {"start_hour": 8, "end_hour": 18},
        "confidence": "HIGH",
        "_count": 60,
    }


class TestVolumeSpike:
    def test_no_trigger_on_normal_volume(self):
        window = {"volume": 10 * 1024 * 1024, "event_count": 5}
        r = vol_eval(window, _profile(10 * 1024 * 1024))
        assert r.triggered is False
        assert r.severity == 0.0

    def test_triggers_on_10x_spike(self):
        window = {"volume": 100 * 1024 * 1024, "event_count": 5}
        r = vol_eval(window, _profile(10 * 1024 * 1024))
        assert r.triggered is True
        assert r.rule == "volume_spike"
        assert 0.0 < r.severity <= 1.0
        assert "individual baseline" in r.explanation

    def test_no_trigger_when_no_baseline(self):
        window = {"volume": 999 * 1024 * 1024, "event_count": 1}
        r = vol_eval(window, None, None)
        assert r.triggered is False

    def test_peer_group_fallback_triggers(self):
        # individual ratio 2x (no trigger), peer ratio 10x → peer-group fires
        window = {"volume": 100 * 1024 * 1024, "event_count": 5}
        r = vol_eval(window, _profile(50 * 1024 * 1024), _profile(10 * 1024 * 1024))
        assert r.triggered is True
        assert "peer-group" in r.explanation


class TestImpossibleTravel:
    def test_same_city_not_triggered(self):
        t1 = NOW
        t2 = NOW + timedelta(hours=2)
        r = travel_eval([(GEO["Chennai"], t1), (GEO["Chennai"], t2)])
        assert r.triggered is False

    def test_distant_logins_minutes_apart_trigger(self):
        # Chennai → Delhi ≈ 1750 km in 20 minutes → far above 600 km/h
        t1 = NOW
        t2 = NOW + timedelta(minutes=20)
        r = travel_eval([(GEO["Chennai"], t1), (GEO["Delhi"], t2)])
        assert r.triggered is True
        assert "Chennai" in r.explanation and "Delhi" in r.explanation
        assert "km/h" in r.explanation

    def test_distant_logins_hours_apart_not_triggered(self):
        t1 = NOW
        t2 = NOW + timedelta(hours=6)
        r = travel_eval([(GEO["Chennai"], t1), (GEO["Delhi"], t2)])
        assert r.triggered is False

    def test_single_login_not_triggered(self):
        r = travel_eval([(GEO["Chennai"], NOW)])
        assert r.triggered is False

    def test_custom_speed_threshold(self):
        t1 = NOW
        t2 = NOW + timedelta(minutes=20)
        # with a 10000 km/h threshold even this gap is "possible"
        r = travel_eval([(GEO["Chennai"], t1), (GEO["Delhi"], t2)], speed_threshold_kmh=10000.0)
        assert r.triggered is False


class TestOutOfScope:
    ACCESS = {"HRMS": "HR", "Finance-DB": "Finance", "git": "Developers"}

    def test_in_scope_not_triggered(self):
        ev = {"entity_id": "EMP001", "target_entity": "HRMS", "file_path": "/HR/HRMS/a.xlsx"}
        r = scope_eval(ev, "HR", self.ACCESS)
        assert r.triggered is False

    def test_out_of_scope_triggered(self):
        ev = {"entity_id": "EMP001", "target_entity": "Finance-DB", "file_path": "/Finance/Finance-DB/a.xlsx"}
        r = scope_eval(ev, "HR", self.ACCESS)
        assert r.triggered is True
        assert r.rule == "out_of_scope"
        assert "Finance" in r.explanation and "HR" in r.explanation
        assert r.severity > 0.0

    def test_unknown_resource_not_triggered(self):
        ev = {"entity_id": "EMP001", "target_entity": "mystery-box", "file_path": ""}
        r = scope_eval(ev, "HR", self.ACCESS)
        assert r.triggered is False

    def test_path_prefix_resolves_dept_without_target(self):
        ev = {"entity_id": "EMP001", "target_entity": "", "file_path": "/Finance/ledger/v2.xlsx"}
        r = scope_eval(ev, "HR", self.ACCESS)
        assert r.triggered is True


class TestDormant:
    ACTIVE = {"start_hour": 8, "end_hour": 18}

    def test_not_dormant_not_triggered(self):
        ev = {"entity_id": "EMP005", "ts": NOW}
        r = dormant_eval(ev, self.ACTIVE, staleness_days=5)
        assert r.triggered is False

    def test_dormant_in_cold_hour_triggered(self):
        ev = {"entity_id": "EMP005", "ts": NOW.replace(hour=2)}
        r = dormant_eval(ev, self.ACTIVE, staleness_days=45)
        assert r.triggered is True
        assert "45" in r.explanation and "02:00" in r.explanation
        assert r.severity > 0.0

    def test_dormant_in_active_hour_not_triggered(self):
        ev = {"entity_id": "EMP005", "ts": NOW.replace(hour=10)}
        r = dormant_eval(ev, self.ACTIVE, staleness_days=45)
        assert r.triggered is False

    def test_unknown_active_window_not_triggered(self):
        ev = {"entity_id": "EMP005", "ts": NOW.replace(hour=2)}
        r = dormant_eval(ev, None, staleness_days=45)
        assert r.triggered is False

    def test_custom_dormant_days(self):
        ev = {"entity_id": "EMP005", "ts": NOW.replace(hour=2)}
        r = dormant_eval(ev, self.ACTIVE, staleness_days=20, dormant_days=30)
        assert r.triggered is False


class TestNovelPeer:
    KNOWN = {"SRV-01", "LPT-001", "APP-01"}

    def test_known_peer_not_triggered(self):
        ev = {"entity_id": "SRV-02", "peer_entity": "LPT-001"}
        r = peer_eval(ev, self.KNOWN, {})
        assert r.triggered is False

    def test_novel_peer_triggered(self):
        ev = {"entity_id": "SRV-02", "peer_entity": "UNKNOWN-42"}
        r = peer_eval(ev, self.KNOWN, {})
        assert r.triggered is True
        assert r.severity == 0.8
        assert "UNKNOWN-42" in r.explanation

    def test_no_peer_field_not_triggered(self):
        ev = {"entity_id": "SRV-02", "peer_entity": ""}
        r = peer_eval(ev, self.KNOWN, {})
        assert r.triggered is False

    def test_peer_with_frequency_scored_lower(self):
        ev = {"entity_id": "SRV-02", "peer_entity": "RARE-99"}
        r = peer_eval(ev, self.KNOWN, {"RARE-99": 1})
        assert r.triggered is True
        assert r.severity == 0.5


class TestRegistry:
    def test_registry_has_all_five_rules(self):
        names = set(rule_names())
        assert names == {"volume_spike", "impossible_travel", "out_of_scope", "dormant", "novel_peer"}

    def test_run_rule_dispatches(self):
        r = run_rule("volume_spike", window={"volume": 100 * 1024 * 1024}, profile_individual=_profile(10 * 1024 * 1024))
        assert isinstance(r, RuleResult)
        assert r.rule == "volume_spike"

    def test_run_rule_unknown_raises(self):
        with pytest.raises(KeyError):
            run_rule("nonexistent")

    def test_not_triggered_result_shape(self):
        r = not_triggered("volume_spike")
        assert r.triggered is False and r.severity == 0.0 and r.explanation == ""