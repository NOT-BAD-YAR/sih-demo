"""Phase 5 — Response engine unit tests (pure logic: recommend/simulate).

The DB-backed `apply()` path is covered in the Phase 5 integration suite.
Covers:
  * the audited action enum + playbook coverage for every detection type;
  * recommend() per anomaly class (LLD mapping);
  * simulate() side-effects, including the isolate_device entity list;
  * unknown detection types / actions degrade safely.
"""

import pytest

from analytics.response import ACTIONS, PLAYBOOK, recommend, simulate


class TestPlaybook:
    def test_actions_enum(self):
        assert ACTIONS == (
            "force_mfa",
            "revoke_session",
            "restrict_access",
            "isolate_device",
            "notify_manager",
            "investigate",
        )

    def test_all_playbook_actions_are_audited(self):
        for alert_type, actions in PLAYBOOK.items():
            for action in actions:
                assert action in ACTIONS, f"{alert_type} -> {action} not audited"

    def test_detection_types_all_covered(self):
        # every anomaly class the runner produces must have a playbook row
        assert set(PLAYBOOK) >= {
            "impossible_travel",
            "volume_spike",
            "out_of_scope",
            "dormant",
            "novel_peer",
            "chain",
        }

    def test_llm_playbook_mapping(self):
        assert PLAYBOOK["impossible_travel"] == ["force_mfa", "revoke_session"]
        assert PLAYBOOK["volume_spike"] == ["restrict_access", "notify_manager"]
        assert PLAYBOOK["out_of_scope"] == ["revoke_session", "restrict_access"]
        assert PLAYBOOK["dormant"] == ["force_mfa", "notify_manager"]
        assert PLAYBOOK["novel_peer"] == ["isolate_device", "investigate"]
        assert PLAYBOOK["chain"] == ["force_mfa", "revoke_session", "isolate_device"]


class TestRecommend:
    def test_known_type(self):
        assert recommend("impossible_travel") == ["force_mfa", "revoke_session"]

    def test_unknown_type_empty(self):
        assert recommend("unknown_anomaly") == []

    def test_returns_a_copy(self):
        first = recommend("chain")
        first.append("notify_manager")
        assert recommend("chain") == ["force_mfa", "revoke_session", "isolate_device"]


class TestSimulate:
    def test_force_mfa(self):
        assert simulate(None, "force_mfa") == {"mfa_forced": True, "next_login_requires": "mfa"}

    def test_revoke_session(self):
        assert simulate([], "revoke_session") == {"sessions_revoked": True}

    def test_isolate_device_lists_entities_sorted(self):
        state = simulate(["EMP5", "EMP1"], "isolate_device")
        assert state["isolated_entity"] == ["EMP1", "EMP5"]

    def test_notify_manager(self):
        assert simulate(None, "notify_manager") == {"manager_notified": True}

    def test_unknown_action_defaults(self):
        assert simulate(None, "self_destruct") == {"simulated": True}