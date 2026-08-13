"""Phase 1 — Anomaly injection tests."""

import random
import pytest
from datetime import datetime, timezone

from simulator.org import generate_org, DEPARTMENT_RESOURCES
from simulator.anomaly import inject_scenario
from simulator.ground_truth import all_records, clear
from simulator.schema import is_valid

NOW = datetime(2026, 6, 1, 10, 0, tzinfo=timezone.utc)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _clean_truth():
    clear()
    yield
    clear()


def _rng(seed: int = 42):
    return random.Random(seed)


class TestAnomalyScenarios:
    @pytest.mark.parametrize(
        "scenario",
        ["volume_spike", "impossible_travel", "out_of_scope", "dormant", "novel_peer", "compromise_chain"],
    )
    def test_each_scenario_generates_events_and_truth(self, scenario):
        org = generate_org(seed=21)
        events = inject_scenario(org, _rng(), scenario, NOW)
        assert events, f"{scenario} produced no events"
        assert all(is_valid(e) for e in events)
        truth = all_records()
        assert [t.scenario for t in truth] == [scenario]
        assert truth[0].expected_risk_band  # band populated

    def test_volume_spike_downloads_large_bytes(self):
        org = generate_org(seed=21)
        events = inject_scenario(org, _rng(), "volume_spike", NOW)
        total = sum(e.bytes_moved for e in events)
        assert total >= 1024 * 1024 * 1024  # at least 1 GB in the spike

    def test_impossible_travel_has_two_logins_two_locations(self):
        org = generate_org(seed=21)
        events = inject_scenario(org, _rng(), "impossible_travel", NOW)
        logins = [e for e in events if e.event_type == "login"]
        assert len(logins) == 2
        assert logins[0].geo["city"] != logins[1].geo["city"]

    def test_out_of_scope_targets_foreign_department(self):
        org = generate_org(seed=21)
        events = inject_scenario(org, _rng(), "out_of_scope", NOW)
        emp_dept = events[0].file_path.split("/")[1]
        org_resources = set(DEPARTMENT_RESOURCES.keys())
        # file_path built from owner dept; ensure the owner dept differs from actor's dept
        actor = events[0].actor
        actor_emp = next(e for e in org.employees if e.emp_id == actor)
        assert emp_dept != actor_emp.department

    def test_dormant_targets_dormant_user_at_night(self):
        org = generate_org(seed=21)
        events = inject_scenario(org, _rng(), "dormant", NOW)
        assert events[0].ts.hour == 2
        assert events[0].ts.minute == 30

    def test_novel_peer_uses_unknown_peer(self):
        org = generate_org(seed=21)
        events = inject_scenario(org, _rng(), "novel_peer", NOW)
        peer = events[0].peer_entity
        server = next(s for s in org.servers if s.server_id == events[0].entity_id)
        assert peer not in server.peers

    def test_compromise_chain_timeline_ordered(self):
        org = generate_org(seed=21)
        events = inject_scenario(org, _rng(), "compromise_chain", NOW)
        ts = [e.ts for e in events]
        assert ts == sorted(ts)
        types = [e.event_type for e in events]
        assert types[0] == "login"
        assert "upload" in types
        assert len(events) >= 5

    def test_unknown_scenario_raises(self):
        org = generate_org(seed=21)
        with pytest.raises(ValueError):
            inject_scenario(org, _rng(), "nonsense", NOW)


class TestScenarioDeterminism:
    def test_same_seed_reproduces_event_ids(self):
        org = generate_org(seed=21)
        a = inject_scenario(org, _rng(77), "compromise_chain", NOW)
        b = inject_scenario(org, _rng(77), "compromise_chain", NOW)
        assert [e.event_id for e in a] == [e.event_id for e in b]