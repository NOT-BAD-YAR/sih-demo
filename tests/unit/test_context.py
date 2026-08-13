"""Phase 4D — Context Engine unit tests.

Covers the 4.5 LLD contract: sensitivity/role/confidence factor tables,
keyword role fallback, hour-of-day risk (incl. past-midnight windows),
department-scope factor via `resource_owner`, and full `ContextVector`
population from both dict and object events.
"""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from analytics.context import (
    build,
    confidence_weight,
    hour_of_day_risk,
    role_factor,
    sensitivity_score,
    DEPT_FACTOR_IN_SCOPE,
    DEPT_FACTOR_OUT_OF_SCOPE,
    HOUR_RISK_IN_WINDOW,
    HOUR_RISK_OUT_WINDOW,
)

pytestmark = pytest.mark.unit

TS = datetime(2026, 1, 15, 10, 30, tzinfo=timezone.utc)
GEO = {"city": "Chennai", "lat": 13.08, "lon": 80.27}
PROFILE = {"confidence": "MED", "active_window": {"start_hour": 8, "end_hour": 18}}


def _event(**overrides) -> dict:
    base = {
        "entity_id": "EMP001",
        "event_type": "download",
        "actor": "EMP001",
        "source_entity": "LPT-001",
        "target_entity": "Finance-DB",
        "peer_entity": "",
        "geo": GEO,
        "ts": TS,
        "bytes_moved": 4096,
        "sensitivity": "confidential",
    }
    base.update(overrides)
    return base


def _user(role: str = "Accountant", department: str = "Finance") -> SimpleNamespace:
    return SimpleNamespace(role=role, department=department)


class TestSensitivityScore:
    def test_mapping_table(self):
        assert sensitivity_score("public") == 0.1
        assert sensitivity_score("internal") == 0.3
        assert sensitivity_score("confidential") == 0.6
        assert sensitivity_score("restricted") == 0.9

    def test_unknown_defaults_to_internal(self):
        assert sensitivity_score(None) == 0.3
        assert sensitivity_score("top-secret") == 0.3


class TestRoleFactor:
    def test_known_titles_map_to_categories(self):
        assert role_factor("HR Manager") == 0.9
        assert role_factor("Tech Lead") == 0.9
        assert role_factor("Accountant") == 0.6
        assert role_factor("Software Engineer") == 0.6

    def test_keyword_fallback(self):
        assert role_factor("System Administrator") == 1.0
        assert role_factor("External Contractor") == 0.8
        assert role_factor("Database Engineer") == 0.9

    def test_empty_defaults_to_staff(self):
        assert role_factor(None) == 0.6
        assert role_factor("") == 0.6


class TestConfidenceWeight:
    def test_grades_map_to_weights(self):
        assert confidence_weight("LOW") == 0.4
        assert confidence_weight("MED") == 0.7
        assert confidence_weight("HIGH") == 1.0

    def test_unknown_defaults_to_low(self):
        assert confidence_weight(None) == 0.4
        assert confidence_weight("nope") == 0.4


class TestHourOfDayRisk:
    def test_inside_active_window_is_low_risk(self):
        assert hour_of_day_risk(10, {"start_hour": 8, "end_hour": 18}) == HOUR_RISK_IN_WINDOW

    def test_outside_active_window_is_high_risk(self):
        assert hour_of_day_risk(2, {"start_hour": 8, "end_hour": 18}) == HOUR_RISK_OUT_WINDOW

    def test_missing_window_defaults_high(self):
        assert hour_of_day_risk(12, None) == HOUR_RISK_OUT_WINDOW

    def test_window_wrapping_past_midnight(self):
        win = {"start_hour": 22, "end_hour": 6}
        assert hour_of_day_risk(23, win) == HOUR_RISK_IN_WINDOW
        assert hour_of_day_risk(3, win) == HOUR_RISK_IN_WINDOW
        assert hour_of_day_risk(12, win) == HOUR_RISK_OUT_WINDOW


class TestBuild:
    def test_populates_all_fields_from_dict_event(self):
        c = build(_event(), PROFILE, _user())
        assert c.who == "EMP001"
        assert c.doing_what == "download -> Finance-DB"
        assert c.using_what == "LPT-001"
        assert c.from_where == "Chennai"
        assert c.when == TS
        assert c.how_much == 4096
        assert c.target_sensitivity == 0.6  # confidential
        assert c.role_factor == 0.6  # Accountant (staff)
        assert c.baseline_confidence == 0.7  # MED
        assert c.hour_of_day_risk == HOUR_RISK_IN_WINDOW  # 10:30 in 8-18

    def test_in_scope_department_factor(self):
        owner = {"Finance-DB": "Finance"}
        c = build(_event(), PROFILE, _user(department="Finance"), resource_owner=owner)
        assert c.dept_factor == DEPT_FACTOR_IN_SCOPE

    def test_out_of_scope_department_factor(self):
        owner = {"Finance-DB": "Finance"}
        c = build(_event(), PROFILE, _user(department="HR"), resource_owner=owner)
        assert c.dept_factor == DEPT_FACTOR_OUT_OF_SCOPE

    def test_no_resource_owner_is_in_scope(self):
        c = build(_event(), PROFILE, _user(department="HR"))
        assert c.dept_factor == DEPT_FACTOR_IN_SCOPE

    def test_cold_start_profile_uses_low_confidence(self):
        c = build(_event(), None, None)
        assert c.baseline_confidence == 0.4
        assert c.role_factor == 0.6
        assert c.dept_factor == DEPT_FACTOR_IN_SCOPE

    def test_off_hours_event_is_high_risk(self):
        late = _event(ts=TS.replace(hour=2))
        c = build(late, PROFILE, _user())
        assert c.hour_of_day_risk == HOUR_RISK_OUT_WINDOW

    def test_accepts_object_event(self):
        ev = SimpleNamespace(**_event())
        c = build(ev, PROFILE, _user())
        assert c.who == "EMP001"
        assert c.doing_what == "download -> Finance-DB"