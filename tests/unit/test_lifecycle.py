"""Phase 5 — Alert/Incident lifecycle + escalation unit tests.

Pure logic — no Docker, no DB. Covers:
  * escalate() tiering: Critical always incident; High+restricted incident;
    High -> assigned alert; Medium/Low -> open alert;
  * create_alert / to_incident escalation with evidence + assignment carry-over;
  * the INCIDENT machine open -> assigned -> investigating -> resolved|fp and
    ALERT machine open -> assigned -> investigating -> resolved|fp;
  * every transition stamps updated_at + updated_by; assign requires analyst;
  * invalid transitions/actions raise ValueError (terminal states are sticky);
  * add_note appends audited entries to incident notes (rejects alerts);
  * role_can RBAC: analyst vs admin action grants, unknown denies.
"""

from datetime import datetime, timezone

import pytest

from analytics.correlation import Incident
from analytics.lifecycle import (
    Alert,
    TERMINAL_STATES,
    add_note,
    assign,
    close,
    create_alert,
    escalate,
    investigate,
    role_can,
    to_incident,
    transition,
)

NOW = datetime(2026, 2, 1, 10, 30, tzinfo=timezone.utc)


class TestEscalate:
    @pytest.mark.parametrize("sensitivity", ["public", "internal", "confidential", "restricted"])
    def test_critical_always_incident(self, sensitivity):
        assert escalate("Critical", sensitivity) == ("incident", True)

    def test_high_restricted_is_incident(self):
        assert escalate("High", "restricted") == ("incident", True)

    @pytest.mark.parametrize("sensitivity", ["public", "internal", "confidential"])
    def test_high_otherwise_assigned_alert(self, sensitivity):
        assert escalate("High", sensitivity) == ("assigned", False)

    @pytest.mark.parametrize("band", ["Medium", "Low"])
    def test_medium_low_open_alert(self, band):
        assert escalate(band, "restricted") == ("open", False)

    def test_unknown_band_is_lenient(self):
        assert escalate("Epic", "restricted") == ("open", False)


class TestCreateAlert:
    def test_default_open(self):
        alert = create_alert("EMP1", 60, "Medium", evidence_refs=["a"], creator="alice", now=NOW)
        assert isinstance(alert, Alert)
        assert alert.status == "open"
        assert alert.assigned_to is None
        assert alert.updated_by == "alice"
        assert alert.created_at == NOW

    def test_incident_needed_preassigns(self):
        alert = create_alert("EMP1", 92, "Critical", incident_needed=True, creator="alice", now=NOW)
        assert alert.status == "assigned"
        assert alert.assigned_to == "alice"

    def test_risk_rounds_to_int(self):
        alert = create_alert("EMP1", 57.6, "High")
        assert alert.risk == 58
        assert alert.severity == "High"

    def test_row_mirrors_table(self):
        alert = create_alert("EMP1", 50, "Medium", evidence_refs=["e1", "e2"])
        assert set(alert.row()) >= {
            "entity_ref", "severity", "risk", "status", "evidence_refs",
            "created_at", "updated_at", "updated_by", "assigned_to",
        }


class TestEscalationToIncident:
    def test_to_incident_carries_fields(self):
        alert = create_alert(
            "EMP1", 90, "Critical", evidence_refs=["e1", "e2"], creator="alice", now=NOW
        )
        incident = to_incident(alert)
        assert isinstance(incident, Incident)
        assert incident.entity_ref == "EMP1"
        assert incident.severity == "Critical"
        assert incident.risk == 90
        assert incident.status == "open"
        assert incident.evidence_refs == ["e1", "e2"]
        assert incident.updated_by == "alice"
        assert incident.created_at == NOW

    def test_to_incident_keeps_assignment(self):
        alert = create_alert("EMP1", 90, "Critical", incident_needed=True, creator="alice", now=NOW)
        incident = to_incident(alert)
        assert incident.assigned_to == "alice"


class TestIncidentMachine:
    def _incident(self):
        return Incident(entity_ref="EMP1", severity="Critical", risk=90, created_at=NOW, updated_at=NOW)

    def test_full_cycle(self):
        inc = self._incident()
        assert assign(inc, analyst_id="bob", actor="alice", now=NOW) is inc
        assert inc.status == "assigned"
        assert inc.assigned_to == "bob"
        assert inc.updated_by == "alice"

        investigate(inc, actor="bob", now=NOW)
        assert inc.status == "investigating"

        close(inc, "resolved", actor="bob", now=NOW)
        assert inc.status == "resolved"

    def test_assign_requires_analyst(self):
        with pytest.raises(ValueError):
            assign(self._incident(), analyst_id=None, actor="alice")

    def test_invalid_forward_transition(self):
        inc = self._incident()
        with pytest.raises(ValueError):
            investigate(inc, actor="alice")  # open -> investigating is illegal

    def test_terminal_states_are_sticky(self):
        inc = self._incident()
        assign(inc, analyst_id="bob", actor="alice", now=NOW)
        close(inc, "false_positive", actor="alice", now=NOW)
        assert inc.status in TERMINAL_STATES
        with pytest.raises(ValueError):
            assign(inc, analyst_id="bob", actor="alice")

    def test_unknown_action_raises(self):
        with pytest.raises(ValueError):
            transition(self._incident(), "explode", "alice")

    def test_invalid_verdict_raises(self):
        with pytest.raises(ValueError):
            close(self._incident(), "maybe", "alice")


class TestAlertMachine:
    def test_open_to_assigned_to_resolved(self):
        alert = create_alert("EMP1", 60, "Medium", creator="alice", now=NOW)
        assign(alert, analyst_id="bob", actor="alice", now=NOW)
        assert alert.status == "assigned"
        investigate(alert, actor="bob", now=NOW)
        assert alert.status == "investigating"
        close(alert, "resolved", actor="bob", now=NOW)
        assert alert.status == "resolved"

    def test_false_positive_verdict(self):
        alert = create_alert("EMP1", 60, "Medium", now=NOW)
        close(alert, "false_positive", actor="bob", now=NOW)
        assert alert.status == "false_positive"

    def test_unsupported_object_type(self):
        with pytest.raises(TypeError):
            transition(object(), "assign", "alice")


class TestAddNote:
    def test_appends_audited_entry(self):
        inc = Incident(entity_ref="EMP1", notes={}, created_at=NOW, updated_at=NOW)
        add_note(inc, analyst_id="bob", text="reviewing", now=NOW)
        entries = inc.notes["entries"]
        assert len(entries) == 1
        assert entries[0]["by"] == "bob"
        assert entries[0]["text"] == "reviewing"
        assert inc.updated_by == "bob"
        assert inc.updated_at == NOW

    def test_requires_text(self):
        inc = Incident(entity_ref="EMP1", created_at=NOW, updated_at=NOW)
        with pytest.raises(ValueError):
            add_note(inc, "bob", "")

    def test_rejects_alerts(self):
        alert = create_alert("EMP1", 60, "Medium", now=NOW)
        with pytest.raises(TypeError):
            add_note(alert, "bob", "note")


class TestRoleCan:
    def test_analyst_can_work(self):
        for action in ("view", "assign", "investigate", "act", "close", "add_note"):
            assert role_can("analyst", action), f"analyst should {action}"

    def test_analyst_cannot_manage(self):
        for action in ("manage", "tune_thresholds"):
            assert not role_can("analyst", action)

    def test_admin_can_everything(self):
        for action in ("view", "assign", "investigate", "act", "close", "add_note", "manage", "tune_thresholds"):
            assert role_can("admin", action)

    def test_unknown_role_and_action_deny(self):
        assert not role_can("ghost", "view")
        assert not role_can("analyst", "explode")