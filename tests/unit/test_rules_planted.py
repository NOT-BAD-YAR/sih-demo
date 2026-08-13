"""Phase 4B — Rule detectors detect every planted anomaly (real simulator data).

Plants each of the 5 canonical scenarios via `simulator.anomaly.inject_scenario`,
feeds them through the processor, and asserts the corresponding rule triggers
with an explainable sentence. This is the Phase 4B exit criterion: "each of the
5 planted anomalies is detected and explained in plain language."
"""

import pytest
import random
from datetime import datetime, timezone
from collections import defaultdict

from simulator.org import generate_org
from simulator.anomaly import inject_scenario
from streaming.producer import normalize_payload
from analytics.processor import validate
from analytics.features import accumulate_all, finalize
from analytics.baseline import build_individual
from analytics.rules.volume_spike import evaluate as vol_eval
from analytics.rules.impossible_travel import evaluate as travel_eval
from analytics.rules.out_of_scope import evaluate as scope_eval
from analytics.rules.dormant import evaluate as dormant_eval
from analytics.rules.novel_peer import evaluate as peer_eval

pytestmark = pytest.mark.unit

NOW = datetime(2026, 1, 20, 10, 30, tzinfo=timezone.utc)


def _normalize(events):
    return [n for n in (validate(normalize_payload(e)) for e in events) if n is not None]


def _merge(norm_events):
    """Collapse normalized events into one finalized window via accumulate_all."""
    wins = accumulate_all(norm_events)
    return [finalize(w) for w in wins.values()]


class TestPlantedAnomaliesDetected:
    def _org(self, seed: int = 1):
        return generate_org(seed=seed)

    def test_volume_spike_anomaly_is_detected(self):
        org = self._org(3)
        planted = inject_scenario(org, random.Random(3), "volume_spike", NOW)
        norm = _normalize(planted)
        assert norm, "planted events must normalize"

        # individual baseline: this employee's normal hourly volume (from backfill)
        emp_id = planted[0].entity_id
        normal = {"window_start": NOW.isoformat(), "volume": 40 * 1024 * 1024, "event_count": 4}
        profile = build_individual(emp_id, [normal])
        spike_window = _merge(norm)[0]
        result = vol_eval(spike_window, profile)
        assert result.triggered, "volume spike rule must fire on the planted spike"
        assert "baseline" in result.explanation.lower()

    def test_impossible_travel_anomaly_is_detected(self):
        org = self._org(5)
        planted = inject_scenario(org, random.Random(5), "impossible_travel", NOW)
        norm = _normalize(planted)
        assert len(norm) >= 2
        pairs = sorted(((ev.geo, ev.ts) for ev in norm if ev.event_type == "login"), key=lambda p: p[1])
        assert len(pairs) >= 2
        result = travel_eval(pairs)
        assert result.triggered, "impossible travel rule must fire on the planted travel"
        assert "km/h" in result.explanation

    def test_out_of_scope_anomaly_is_detected(self):
        org = self._org(7)
        planted = inject_scenario(org, random.Random(7), "out_of_scope", NOW)
        norm = _normalize(planted)
        assert norm
        ev = norm[0]
        emp = next(e for e in org.employees if e.emp_id == ev.entity_id)
        result = scope_eval(ev.__dict__, emp.department, org.resource_owner)
        assert result.triggered, "out-of-scope rule must fire on the planted access"
        assert "department scope" in result.explanation

    def test_dormant_anomaly_is_detected(self):
        org = self._org(9)
        planted = inject_scenario(org, random.Random(9), "dormant", NOW)
        norm = _normalize(planted)
        assert norm
        ev = norm[0]
        emp = next(e for e in org.employees if e.emp_id == ev.entity_id)
        assert emp.dormant
        result = dormant_eval(
            ev.__dict__, {"start_hour": 8, "end_hour": 18}, staleness_days=45
        )
        assert result.triggered, "dormant rule must fire on the planted activation"
        assert "Dormant" in result.explanation and "02:00" in result.explanation

    def test_novel_peer_anomaly_is_detected(self):
        org = self._org(11)
        planted = inject_scenario(org, random.Random(11), "novel_peer", NOW)
        norm = _normalize(planted)
        assert norm
        ev = norm[0]
        srv = next(s for s in org.servers if s.server_id == ev.entity_id)
        result = peer_eval(ev.__dict__, srv.peers, {})
        assert result.triggered, "novel-peer rule must fire on the planted peer"
        assert ev.peer_entity in result.explanation


class TestRuleRegistryAgainstScenarios:
    def test_every_planted_scenario_has_a_rule(self):
        from analytics.rules import rule_names

        names = set(rule_names())
        for scenario in ("volume_spike", "impossible_travel", "out_of_scope", "dormant", "novel_peer"):
            assert scenario in names, f"{scenario} must have a registered rule"