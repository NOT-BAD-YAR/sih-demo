"""Shared configuration for the UEBA platform.

Every module reads its settings from this single source. Phase 0 owns the
dataclass + env loading; later phases consume `Config.from_env()`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    """Central configuration, populated from environment variables.

    Values mirror `.env.example`. Tuning parameters used by the engine
    (Phases 4+) are declared here so thresholds stay centralized.
    """

    # --- infrastructure ---
    kafka_bootstrap: str = "localhost:9092"
    kafka_group_id: str = "analytics-engine"
    postgres_dsn: str = "postgresql+psycopg2://ueba:ueba_secret@localhost:5432/ueba"

    # --- behavioural baselines ---
    warmup_days: int = 14
    primary_window_days: int = 30
    long_term_days: int = 90

    # --- rule engine thresholds ---
    rule_volume_threshold_k: float = 5.0
    impossible_travel_speed_kmh: float = 600.0
    dormant_days: int = 30

    # --- risk bands ---
    risk_band_high: int = 50
    risk_band_critical: int = 75

    # --- feature flags (used by tests/eval, Phases 4C/9) ---
    enable_rules: bool = True
    enable_ml: bool = True

    @classmethod
    def from_env(cls, env: dict | None = None) -> "Config":
        """Build a Config from environment variables.

        Accepts an optional mapping (used by tests and the in-process
        simulator) but defaults to the real process environment, so callers
        should not need to pass anything in production.
        """
        source = env if env is not None else os.environ

        def _int(name: str, default: int) -> int:
            raw = source.get(name)
            return int(raw) if raw else default

        def _float(name: str, default: float) -> float:
            raw = source.get(name)
            return float(raw) if raw else default

        def _bool(name: str, default: bool) -> bool:
            raw = source.get(name)
            if raw is None:
                return default
            return raw.strip().lower() in {"1", "true", "yes", "on"}

        return cls(
            kafka_bootstrap=source.get("KAFKA_BOOTSTRAP", "localhost:9092"),
            kafka_group_id=source.get("KAFKA_GROUP_ID", "analytics-engine"),
            postgres_dsn=source.get(
                "POSTGRES_DSN",
                "postgresql+psycopg2://ueba:ueba_secret@localhost:5432/ueba",
            ),
            warmup_days=_int("WARMUP_DAYS", 14),
            primary_window_days=_int("PRIMARY_WINDOW_DAYS", 30),
            long_term_days=_int("LONG_TERM_DAYS", 90),
            rule_volume_threshold_k=_float("RULE_VOLUME_THRESHOLD_K", 5.0),
            impossible_travel_speed_kmh=_float("IMPOSSIBLE_TRAVEL_SPEED_KMH", 600.0),
            dormant_days=_int("DORMANT_DAYS", 30),
            risk_band_high=_int("RISK_BAND_HIGH", 50),
            risk_band_critical=_int("RISK_BAND_CRITICAL", 75),
            enable_rules=_bool("ENABLE_RULES", True),
            enable_ml=_bool("ENABLE_ML", True),
        )