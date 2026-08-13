"""Phase 1 — Ground-truth records tests."""

import pytest

from simulator.ground_truth import record, all_records, clear, GroundTruthRecord

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _clean():
    clear()
    yield
    clear()


class TestGroundTruth:
    def test_record_appends(self):
        record("volume_spike", "EMP001", "2026-01-01T00:00:00", "2026-01-01T00:15:00")
        recs = all_records()
        assert len(recs) == 1
        assert recs[0].scenario == "volume_spike"
        assert recs[0].entity_id == "EMP001"

    def test_clear_empties(self):
        record("dormant", "EMP005", "x", "y")
        clear()
        assert all_records() == []

    def test_related_events_stored(self):
        record("compromise_chain", "EMP003", "a", "b", related_event_ids=["ev1", "ev2"])
        assert all_records()[0].related_event_ids == ["ev1", "ev2"]

    def test_to_dict_shape(self):
        record("novel_peer", "SRV-03", "s", "e", rule="novel_peer", expected_risk_band="Medium")
        d = all_records()[0].to_dict()
        assert set(d) == {"scenario", "entity_id", "start", "end", "related_event_ids", "rule", "expected_risk_band"}
        assert d["expected_risk_band"] == "Medium"