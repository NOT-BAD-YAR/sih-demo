"""Phase 4E — Correlation Engine unit tests.

Covers resolve_chain / score_event / maintain_incident / cluster_for_entity:
  * chain extraction from NormalizedEvent, dicts, and ScoredEvent (dedup, order);
  * incident creation rules: >= CHAIN_MIN_LINKS distinct chain entities OR a
    single Critical event opens an incident; otherwise none;
  * escalation/merge semantics: a new event folds into the already-open
    incident that shares a chain entity — across entity boundaries too;
  * the multi-stage Account Compromise sequence collapses into ONE incident
    with every evidence ref and the full entity chain.

Pure logic — no Docker, no DB.
"""

from datetime import datetime, timedelta, timezone

import pytest

from analytics.correlation import (
    CRITICAL_THRESHOLD,
    ScoredEvent,
    Incident,
    cluster_for_entity,
    maintain_incident,
    resolve_chain,
    score_event,
)
from analytics.processor import NormalizedEvent

NOW = datetime(2026, 2, 1, 10, 30, tzinfo=timezone.utc)


def _scored(event_id, entity_ref, minutes, risk, chain, severity=0.8):
    return ScoredEvent(
        event_id=event_id,
        entity_ref=entity_ref,
        ts=NOW + timedelta(minutes=minutes),
        risk=float(risk),
        severity=severity,
        chain=list(chain),
        alert_id=f"ALERT-{event_id}",
    )


def _event(**kwargs) -> NormalizedEvent:
    base = dict(
        event_id="ev1",
        ts=NOW,
        ingested_at=NOW,
        entity_type="user",
        entity_id="u1",
        user_id="u1",
        event_type="login",
        actor="u1",
        source_entity="dev1",
        target_entity="dev1",
        peer_entity="",
        ip="10.0.0.1",
        geo={},
        file_path=None,
        bytes_moved=0,
        outcome="success",
        sensitivity="internal",
    )
    base.update(kwargs)
    return NormalizedEvent(**base)


class TestResolveChain:
    def test_actor_source_target_peer_order(self):
        ev = _event(actor="u1", source_entity="dev1", target_entity="res1", peer_entity="peer1")
        assert resolve_chain(ev) == ["u1", "dev1", "res1", "peer1"]

    def test_empty_values_filtered(self):
        ev = _event(actor="u1", source_entity="", target_entity="res1", peer_entity="")
        assert resolve_chain(ev) == ["u1", "res1"]

    def test_dedupes_while_preserving_order(self):
        ev = _event(actor="u1", source_entity="dev1", target_entity="u1", peer_entity="dev1")
        assert resolve_chain(ev) == ["u1", "dev1"]

    def test_plain_dict_event_supported(self):
        d = {"actor": "u1", "source_entity": "dev1", "target_entity": "", "peer_entity": "peer1"}
        assert resolve_chain(d) == ["u1", "dev1", "peer1"]

    def test_scored_event_chain_passthrough(self):
        se = _scored("e1", "u1", 0, 50.0, ["u1", "dev1"])
        assert resolve_chain(se) == ["u1", "dev1"]


class TestScoreEvent:
    def test_wraps_normalized_event_with_risk(self):
        ev = _event(event_id="e9", actor="u1", source_entity="dev1", target_entity="res1")
        se = score_event(ev, risk=88.5, severity=0.9, alert_id="A9")
        assert se.event_id == "e9"
        assert se.entity_ref == "u1"
        assert se.ts == NOW
        assert se.risk == 88.5
        assert se.chain == ["u1", "dev1", "res1"]
        assert se.alert_id == "A9"
        assert se.band == "Critical"

    def test_band_from_risk(self):
        ev = _event(event_id="e2", actor="u1", source_entity="dev1")
        assert score_event(ev, risk=30.0).band == "Medium"
        assert score_event(ev, risk=60.0).band == "High"
        assert score_event(ev, risk=10.0).band == "Low"


class TestMaintainIncident:
    def test_appends_evidence_and_alert_ids_deduped(self):
        inc = Incident(created_at=NOW, updated_at=NOW)
        maintain_incident(inc, _scored("e1", "u1", 0, 50.0, ["u1", "d1"]))
        maintain_incident(inc, _scored("e1", "u1", 1, 55.0, ["u1", "d1"]))
        assert inc.evidence_refs == ["e1"]
        assert inc.related_alert_ids == ["ALERT-e1"]

    def test_recomputes_max_risk_and_severity(self):
        inc = Incident(created_at=NOW, updated_at=NOW)
        maintain_incident(inc, _scored("e1", "u1", 0, 40.0, ["u1", "d1"]))
        assert inc.risk == 40 and inc.severity == "Medium"
        maintain_incident(inc, _scored("e2", "u1", 1, 90.0, ["u1", "d1"]))
        assert inc.risk == 90 and inc.severity == "Critical"
        maintain_incident(inc, _scored("e3", "u1", 2, 60.0, ["u1", "d1"]))
        assert inc.risk == 90  # max preserved

    def test_extends_chain_sorted_union(self):
        inc = Incident(created_at=NOW, updated_at=NOW)
        maintain_incident(inc, _scored("e1", "u1", 0, 50.0, ["u1", "d1"]))
        maintain_incident(inc, _scored("e2", "u1", 1, 50.0, ["u1", "d1", "res"]))
        assert inc.entity_chain == sorted(["u1", "d1", "res"])

    def test_tracks_latest_update(self):
        inc = Incident(created_at=NOW, updated_at=NOW)
        maintain_incident(inc, _scored("e1", "u1", 5, 50.0, ["u1", "d1"]))
        assert inc.updated_at == NOW + timedelta(minutes=5)


class TestClusterForEntity:
    def test_empty_window_returns_none(self):
        assert cluster_for_entity("u1", [], []) is None

    def test_single_entity_low_risk_returns_none(self):
        ev = _scored("e1", "u1", 0, 30.0, ["u1"])
        assert cluster_for_entity("u1", [ev], []) is None

    def test_chain_of_two_entities_creates_incident(self):
        ev = _scored("e1", "u1", 0, 40.0, ["u1", "dev1"])
        inc = cluster_for_entity("u1", [ev], [])
        assert inc is not None
        assert inc.evidence_refs == ["e1"]
        assert inc.risk == 40 and inc.entity_chain == sorted(["u1", "dev1"])

    def test_single_critical_alert_creates_incident(self):
        ev = _scored("e1", "u1", 0, 80.0, ["u1"])
        inc = cluster_for_entity("u1", [ev], [])
        assert inc is not None and inc.severity == "Critical"
        assert CRITICAL_THRESHOLD <= 80.0

    def test_below_critical_threshold_constant(self):
        ev = _scored("e1", "u1", 0, CRITICAL_THRESHOLD, ["u1"])
        assert cluster_for_entity("u1", [ev], []) is not None

    def test_new_incident_uses_first_entity_ref(self):
        ev = _scored("e1", "u1", 0, 60.0, ["u1", "dev1"])
        inc = cluster_for_entity("u1", [ev], [])
        assert inc.entity_ref == "u1"

    def test_window_risk_is_the_maximum(self):
        evs = [
            _scored("e1", "u1", 0, 55.0, ["u1", "d1"]),
            _scored("e2", "u1", 1, 72.0, ["u1", "d1", "res"]),
        ]
        inc = cluster_for_entity("u1", evs, [])
        assert inc.risk == 72 and inc.severity == "High"

    def test_merges_into_open_incident_sharing_chain(self):
        first = _scored("e1", "u1", 0, 60.0, ["u1", "THREAT-DEVICE"])
        inc = cluster_for_entity("u1", [first], [])
        second = _scored("e2", "u1", 3, 80.0, ["u1", "THREAT-DEVICE", "res"])
        merged = cluster_for_entity("u1", [second], [inc])
        assert merged is inc  # escalated in place, not duplicated
        assert inc.evidence_refs == ["e1", "e2"]
        assert inc.risk == 80 and inc.severity == "Critical"

    def test_cross_entity_fold(self):
        # Event A belongs to emp; event B belongs to the THREAT-DEVICE entity.
        # They share the THREAT-DEVICE chain edge, so B must fold into A.
        a = _scored("e1", "emp1", 0, 65.0, ["emp1", "THREAT-DEVICE"])
        inc = cluster_for_entity("emp1", [a], [])
        b = _scored("e2", "THREAT-DEVICE", 1, 75.0, ["THREAT-DEVICE", "emp1-device"])
        merged = cluster_for_entity("THREAT-DEVICE", [b], [inc])
        assert merged is inc
        assert inc.evidence_refs == ["e1", "e2"]
        assert "emp1-device" in inc.entity_chain

    def test_does_not_merge_resolved_incidents(self):
        first = _scored("e1", "u1", 0, 60.0, ["u1", "THREAT-DEVICE"])
        inc = cluster_for_entity("u1", [first], [])
        inc.status = "resolved"
        second = _scored("e2", "u2", 1, 80.0, ["u2", "THREAT-DEVICE"])
        out = cluster_for_entity("u2", [second], [inc])
        assert out is not inc
        assert out.evidence_refs == ["e2"]

    def test_compromise_chain_folds_into_one_incident(self):
        # The multi-stage Account Compromise sequence: login@new-loc -> new
        # device -> sensitive access -> big download -> external upload.
        emp_events = [
            _scored("e1", "emp1", 0, 60.0, ["emp1", "THREAT-DEVICE"]),
            _scored("e3", "emp1", 3, 78.0, ["emp1", "THREAT-DEVICE", "client_list.xlsx"]),
            _scored("e4", "emp1", 8, 84.0, ["emp1", "THREAT-DEVICE", "bulk"]),
            _scored("e5", "emp1", 11, 90.0, ["emp1", "THREAT-DEVICE", "STORAGE.EXTERNAL.CLOUD"]),
        ]
        inc = cluster_for_entity("emp1", emp_events, [])
        assert inc is not None
        # the USB event belongs to the device entity and still folds in
        device_event = _scored("e2", "THREAT-DEVICE", 1, 70.0, ["THREAT-DEVICE", "emp1-device"])
        inc2 = cluster_for_entity("THREAT-DEVICE", [device_event], [inc])
        assert inc2 is inc

        assert len(inc.evidence_refs) == 5, inc.evidence_refs
        assert len(inc.related_alert_ids) == 5
        assert "emp1" in inc.entity_chain
        assert "THREAT-DEVICE" in inc.entity_chain
        assert "STORAGE.EXTERNAL.CLOUD" in inc.entity_chain
        assert inc.risk == 90 and inc.severity == "Critical"
        # every evidence timestamp falls within one 30-minute rolling window
        assert inc.updated_at - inc.created_at <= timedelta(minutes=30)