"""Phase 4A — Feature Engine (windowed feature accumulation) tests."""

import pytest
from datetime import datetime, timezone, timedelta

from analytics.features import (
    accumulate,
    accumulate_all,
    finalize,
    haversine_km,
    hour_bucket,
    staleness_before,
)
from analytics.processor import validate
from simulator.schema import build_event
from streaming.producer import normalize_payload

pytestmark = pytest.mark.unit

NOW = datetime(2026, 1, 15, 10, 30, tzinfo=timezone.utc)


def _ev(**over):
    base = dict(
        entity_type="user",
        entity_id="EMP001",
        user_id="EMP001",
        event_type="login",
        actor="EMP001",
        source_entity="LPT-001",
        target_entity="LPT-001",
        ts=NOW,
        ip="10.0.0.1",
        geo={"city": "Chennai", "lat": 13.08, "lon": 80.27},
    )
    base.update(over)
    return validate(normalize_payload(build_event(**base)))


class TestHourBucket:
    def test_floors_to_hour(self):
        assert hour_bucket(NOW) == datetime(2026, 1, 15, 10, 0, tzinfo=timezone.utc)

    def test_exact_hour_unchanged(self):
        exact = datetime(2026, 1, 15, 10, 0, tzinfo=timezone.utc)
        assert hour_bucket(exact) == exact


class TestAccumulate:
    def test_single_event_window(self):
        w = accumulate(None, _ev())
        assert w.entity_ref == "EMP001"
        assert w.event_count == 1
        assert w.volume == 0
        assert w.window_start == hour_bucket(NOW)

    def test_accumulates_volume_and_count(self):
        w = accumulate(None, _ev(event_type="download", bytes_moved=1024))
        w = accumulate(w, _ev(event_type="download", bytes_moved=2048, ts=NOW + timedelta(minutes=5)))
        assert w.event_count == 2
        assert w.volume == 3072

    def test_unique_peers_are_sets(self):
        w = accumulate(None, _ev(event_type="network_conn", peer_entity="SRV-01"))
        w = accumulate(w, _ev(event_type="network_conn", peer_entity="SRV-01"))
        w = accumulate(w, _ev(event_type="network_conn", peer_entity="SRV-02"))
        assert w.unique_peers == {"SRV-01", "SRV-02"}

    def test_location_count_and_sensitivity_hist(self):
        w = accumulate(None, _ev(sensitivity="internal"))
        w = accumulate(w, _ev(sensitivity="confidential", geo={"city": "Delhi", "lat": 28.61, "lon": 77.21}))
        vector = finalize(w)
        assert vector["location_count"] == 2
        assert vector["sensitivity_hist"] == {"internal": 1, "confidential": 1}

    def test_fail_rate(self):
        w = accumulate(None, _ev(event_type="failure", outcome="failure"))
        w = accumulate(w, _ev(outcome="success"))
        assert finalize(w)["fail_rate"] == pytest.approx(0.5)

    def test_dept_distinct_from_file_path(self):
        w = accumulate(None, _ev(event_type="file_access", file_path="/Finance/ledger/a.xlsx"))
        w = accumulate(w, _ev(event_type="file_access", file_path="/HR/hrms/b.xlsx"))
        assert finalize(w)["dept_distinct"] == ["Finance", "HR"]

    def test_active_hours_frac_from_span(self):
        w = accumulate(None, _ev(ts=NOW))
        w = accumulate(w, _ev(ts=NOW + timedelta(minutes=30)))
        assert finalize(w)["active_hours_frac"] == pytest.approx(0.5)


class TestFinalize:
    def test_finalize_returns_jsonb_vector(self):
        w = accumulate(None, _ev(event_type="download", bytes_moved=5000, peer_entity="SRV-01"))
        vector = finalize(w)
        assert vector["entity_ref"] == "EMP001"
        assert vector["volume"] == 5000
        assert vector["event_count"] == 1
        assert "window_start" in vector
        assert isinstance(vector["unique_peers"], list)
        assert isinstance(vector["dept_distinct"], list)
        assert isinstance(vector["sensitivity_hist"], dict)

    def test_finalize_is_idempotent(self):
        w = accumulate(None, _ev())
        first = finalize(w)
        second = finalize(w)
        assert first == second

    def test_geo_pairwise_distance(self):
        w = accumulate(None, _ev(geo={"city": "Chennai", "lat": 13.08, "lon": 80.27}))
        w = accumulate(w, _ev(geo={"city": "Delhi", "lat": 28.61, "lon": 77.21}))
        vector = finalize(w)
        assert vector["location_count"] == 2
        assert vector["location_dist_km"] > 1600  # Chennai → Delhi ~1750 km


class TestAccumulateAll:
    def test_windows_keyed_by_entity_and_hour(self):
        events = [
            _ev(ts=NOW),
            _ev(ts=NOW),
            _ev(ts=NOW + timedelta(hours=1)),
        ]
        windows = accumulate_all(events)
        assert len(windows) == 2  # two distinct hour buckets for same entity

    def test_different_entities_separated(self):
        events = [
            _ev(ts=NOW),
            _ev(entity_id="EMP002", user_id="EMP002", actor="EMP002", ts=NOW),
        ]
        windows = accumulate_all(events)
        assert len(windows) == 2


class TestStaleness:
    def test_staleness_days(self):
        last = NOW - timedelta(days=45)
        w = accumulate(None, _ev(ts=NOW))
        assert staleness_before(w, last) == 45

    def test_staleness_unknown_is_zero(self):
        w = accumulate(None, _ev(ts=NOW))
        assert staleness_before(w, None) == 0


class TestHaversine:
    def test_same_point_is_zero(self):
        assert haversine_km(13.08, 80.27, 13.08, 80.27) == pytest.approx(0.0)

    def test_chennai_delhi_approx(self):
        d = haversine_km(13.08, 80.27, 28.61, 77.21)
        assert 1600 < d < 1900