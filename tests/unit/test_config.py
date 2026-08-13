"""Config unit tests — analytics/config.py.

Phase 0 delivers the central Config; later phases depend on its defaults and
env overrides being correct. Tested without any external services.
"""

import pytest

from analytics.config import Config


@pytest.mark.unit
class TestConfigDefaults:
    def test_defaults_match_documented_values(self):
        cfg = Config.from_env({})
        # infra
        assert cfg.kafka_bootstrap == "localhost:9092"
        assert cfg.kafka_group_id == "analytics-engine"
        assert "postgresql" in cfg.postgres_dsn
        # baselines
        assert cfg.warmup_days == 14
        assert cfg.primary_window_days == 30
        assert cfg.long_term_days == 90
        # rules
        assert cfg.rule_volume_threshold_k == 5.0
        assert cfg.impossible_travel_speed_kmh == 600.0
        assert cfg.dormant_days == 30
        # risk
        assert cfg.risk_band_high == 50
        assert cfg.risk_band_critical == 75
        # flags
        assert cfg.enable_rules is True
        assert cfg.enable_ml is True

    def test_frozen_immutability(self):
        cfg = Config.from_env({})
        with pytest.raises(Exception):
            cfg.kafka_bootstrap = "override"  # frozen dataclass -> error


@pytest.mark.unit
class TestConfigEnvOverrides:
    def test_string_override(self):
        cfg = Config.from_env({"KAFKA_BOOTSTRAP": "broker:19092"})
        assert cfg.kafka_bootstrap == "broker:19092"

    def test_dsn_override(self):
        cfg = Config.from_env({"POSTGRES_DSN": "postgresql://u:p@h:5433/d"})
        assert cfg.postgres_dsn == "postgresql://u:p@h:5433/d"

    def test_int_override(self):
        cfg = Config.from_env({"WARMUP_DAYS": "7", "RISK_BAND_HIGH": "60"})
        assert cfg.warmup_days == 7
        assert cfg.risk_band_high == 60

    def test_float_override(self):
        cfg = Config.from_env({"IMPOSSIBLE_TRAVEL_SPEED_KMH": "900.5"})
        assert cfg.impossible_travel_speed_kmh == 900.5

    def test_bool_overrides(self):
        cfg = Config.from_env({"ENABLE_RULES": "false", "ENABLE_ML": "0"})
        assert cfg.enable_rules is False
        assert cfg.enable_ml is False

    def test_bool_truthy_variants(self):
        for value in ("1", "true", "yes", "on"):
            cfg = Config.from_env({"ENABLE_ML": value})
            assert cfg.enable_ml is True


@pytest.mark.unit
class TestConfigFromRealEnv:
    def test_source_defaults_to_os_environ(self, monkeypatch):
        monkeypatch.delenv("KAFKA_GROUP_ID", raising=False)
        monkeypatch.setenv("KAFKA_BOOTSTRAP", "realhost:9092")
        cfg = Config.from_env()
        assert cfg.kafka_bootstrap == "realhost:9092"
        assert cfg.kafka_group_id == "analytics-engine"