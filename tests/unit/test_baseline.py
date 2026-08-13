"""Phase 4A — Baseline Engine (confidence, builder, cold start, retrain) tests."""

import pytest
from datetime import datetime, timezone, timedelta

from analytics.baseline import (
    confidence_for,
    build_individual,
    build_peer_group,
    build_global,
    select_level,
    rolling_retrain,
    NUMERIC_FEATURES,
)

pytestmark = pytest.mark.unit

W0 = datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc)


def _window(hour: int, *, volume: int = 1000, dept: str = "HR", peer: str = "SRV-01", day: int = 1) -> dict:
    return {
        "entity_ref": "EMP001",
        "window_start": datetime(2026, 1, day, hour % 24, 0, tzinfo=timezone.utc).isoformat(),
        "volume": volume,
        "event_count": 5,
        "active_hours_frac": 0.8,
        "unique_peers": [peer],
        "new_peer_count": 0,
        "location_count": 1,
        "location_dist_km": 0.0,
        "dept_distinct": [dept],
        "sensitivity_hist": {"internal": 4, "confidential": 1},
        "fail_rate": 0.02,
        "staleness_days": 0,
    }


class TestConfidence:
    def test_low_below_20(self):
        assert confidence_for(0) == "LOW"
        assert confidence_for(19) == "LOW"

    def test_medium_20_to_100(self):
        assert confidence_for(20) == "MED"
        assert confidence_for(100) == "MED"

    def test_high_above_100(self):
        assert confidence_for(101) == "HIGH"


class TestBuildIndividual:
    def test_builds_stats_for_all_numeric_features(self):
        windows = [_window(9, volume=1000), _window(10, volume=3000), _window(11, volume=2000)]
        row = build_individual("EMP001", windows)
        assert row["level"] == "individual"
        assert row["entity_ref"] == "EMP001"
        for f in NUMERIC_FEATURES:
            assert f in row["feature_stats"], f"missing feature stat: {f}"
        assert row["feature_stats"]["volume"]["mean"] == pytest.approx(2000.0)
        assert row["feature_stats"]["volume"]["count"] == 3

    def test_confidence_from_window_count(self):
        row = build_individual("EMP001", [_window(h) for h in range(9, 9 + 5)])
        assert row["confidence"] == "LOW"
        assert row["_count"] == 5

    def test_allowed_sets_unioned(self):
        windows = [_window(9, dept="HR", peer="SRV-01"), _window(10, dept="Finance", peer="SRV-02")]
        row = build_individual("EMP001", windows)
        assert set(row["allowed_sets"]["peers"]) == {"SRV-01", "SRV-02"}
        assert set(row["allowed_sets"]["dept_paths"]) == {"HR", "Finance"}
        assert set(row["allowed_sets"]["sensitivity"]) == {"internal", "confidential"}

    def test_active_window_derived_from_hours(self):
        row = build_individual("EMP001", [_window(8), _window(9), _window(18)])
        assert row["active_window"]["start_hour"] == 8
        assert row["active_window"]["end_hour"] == 18

    def test_empty_windows_still_forms_row(self):
        row = build_individual("EMP001", [])
        assert row["confidence"] == "LOW"
        assert row["_count"] == 0
        assert row["feature_stats"]["volume"]["count"] == 0


class TestBuildPeerGroupAndGlobal:
    def _members(self, n: int = 4):
        return [
            build_individual(f"EMP{i:03d}", [_window(9, volume=1000), _window(10, volume=2000)])
            for i in range(1, n + 1)
        ]

    def test_peer_group_aggregates_counts(self):
        members = self._members(4)
        row = build_peer_group("HR", members)
        assert row["level"] == "peer_group"
        assert row["entity_ref"] == "HR"
        assert row["_count"] == 8  # 4 members x 2 windows
        assert row["feature_stats"]["volume"]["count"] == 8

    def test_peer_group_unions_sets(self):
        members = [
            build_individual("EMP001", [_window(9, dept="HR", peer="SRV-01")]),
            build_individual("EMP002", [_window(9, dept="Finance", peer="SRV-02")]),
        ]
        row = build_peer_group("HR", members)
        assert set(row["allowed_sets"]["peers"]) == {"SRV-01", "SRV-02"}

    def test_global_aggregates_all(self):
        members = self._members(6)
        row = build_global(members)
        assert row["level"] == "global"
        assert row["entity_ref"] == "__global__"
        assert row["_count"] == 12

    def test_empty_members_is_empty_aggregate(self):
        assert build_peer_group("HR", [])["_count"] == 0
        assert build_global([])["_count"] == 0


class TestSelectLevel:
    def _profiles(self, individual_windows: int, peer_count: int):
        individual = (
            build_individual("EMP001", [_window(h) for h in range(9, 9 + individual_windows)])
            if individual_windows
            else build_individual("EMP001", [])
        )
        peers = [
            build_individual(f"EMP{i:03d}", [_window(9)])
            for i in range(1, peer_count + 1)
        ]
        peer = build_peer_group("HR", peers)
        global_row = build_global([individual] + peers)
        return {"individual": individual, "peer_group": peer, "global": global_row}

    def test_rich_individual_is_chosen(self):
        profiles = self._profiles(individual_windows=25, peer_count=4)
        level, row = select_level("EMP001", profiles)
        assert level == "individual"
        assert row["entity_ref"] == "EMP001"

    def test_sparse_individual_falls_back_to_peer_group(self):
        profiles = self._profiles(individual_windows=5, peer_count=5)
        level, row = select_level("EMP001", profiles)
        assert level == "peer_group"
        assert row["entity_ref"] == "HR"

    def test_sparse_everything_falls_back_to_global(self):
        profiles = self._profiles(individual_windows=0, peer_count=0)
        level, row = select_level("EMP001", profiles)
        assert level == "global"
        assert row["entity_ref"] == "__global__"

    def test_no_baseline_raises(self):
        with pytest.raises(ValueError):
            select_level("EMP001", {"individual": None, "peer_group": None, "global": None})


class TestRollingRetrain:
    def test_rebuilds_from_recent_windows(self):
        now = datetime.now(timezone.utc)
        old = datetime.now(timezone.utc) - timedelta(days=60)
        windows = [
            {"entity_ref": "EMP001", "window_start": old.isoformat(), "volume": 100,
             "event_count": 5, "active_hours_frac": 0.5, "unique_peers": ["SRV-01"],
             "new_peer_count": 0, "location_count": 1, "location_dist_km": 0.0,
             "dept_distinct": ["HR"], "sensitivity_hist": {"internal": 5},
             "fail_rate": 0.0, "staleness_days": 0},
            {"entity_ref": "EMP001", "window_start": now.isoformat(), "volume": 4000,
             "event_count": 5, "active_hours_frac": 0.5, "unique_peers": ["SRV-01"],
             "new_peer_count": 0, "location_count": 1, "location_dist_km": 0.0,
             "dept_distinct": ["HR"], "sensitivity_hist": {"internal": 5},
             "fail_rate": 0.0, "staleness_days": 0},
        ]
        row = rolling_retrain("EMP001", windows, last_n_days=30)
        assert row["_count"] == 1  # only the recent window kept
        assert row["feature_stats"]["volume"]["mean"] == pytest.approx(4000.0)