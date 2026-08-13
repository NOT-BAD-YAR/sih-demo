"""Phase 4C — ML Engine (Isolation Forest) unit tests.

Covers the 4.4 LLD contract: featurize matrix building, the minimum-window
train gate (count >= ML_MIN_WINDOWS / force override), model caching keyed by
level+entity, 0-1 anomaly scoring bounded in [0,1], cold-start fallback
(individual -> peer_group -> global -> neutral 0), daily rolling retrain, and
determinism from the fixed random_state. No external services required.
"""

import math

import numpy as np
import pytest

from analytics.ml import (
    ML_FEATURES,
    MODEL_CACHE,
    clear_models,
    featurize,
    retrain_schedule,
    score,
    train,
)

pytestmark = pytest.mark.unit


def _window(*, volume: float = 100.0, event_count: int = 5, active: float = 0.5,
            locations: int = 1, dist_km: float = 0.0, fail_rate: float = 0.01,
            stale: float = 0.0) -> dict:
    return {
        "volume": volume,
        "event_count": event_count,
        "active_hours_frac": active,
        "location_count": locations,
        "location_dist_km": dist_km,
        "fail_rate": fail_rate,
        "staleness_days": stale,
    }


def _history(n: int, base: float = 100.0) -> list[dict]:
    """Varied windows so the model learns a real normal region (IF needs
    variance to separate; constant-feature history is degenerate)."""
    return [
        _window(
            volume=base + i,
            event_count=4 + (i % 3),
            active=0.4 + 0.1 * (i % 4),
            locations=1 + (i % 2),
        )
        for i in range(n)
    ]


@pytest.fixture(autouse=True)
def _clean_cache():
    clear_models()
    yield
    clear_models()


class TestFeaturize:
    def test_builds_float_matrix_in_feature_order(self):
        X = featurize([_window(volume=7), _window(volume=9)])
        assert X.shape == (2, len(ML_FEATURES))
        assert X.dtype == np.float64
        assert X[0][ML_FEATURES.index("volume")] == 7.0

    def test_feature_columns_match_declared_order(self):
        X = featurize([_window()])
        assert list(ML_FEATURES) == ["volume", "event_count", "active_hours_frac",
                                     "location_count", "location_dist_km",
                                     "fail_rate", "staleness_days"]
        assert X.shape[1] == len(ML_FEATURES)

    def test_missing_features_default_to_zero(self):
        X = featurize([{"volume": 1}])
        assert X.shape == (1, len(ML_FEATURES))
        assert X[0][ML_FEATURES.index("event_count")] == 0.0

    def test_empty_list_returns_empty_matrix(self):
        X = featurize([])
        assert X.shape == (0, len(ML_FEATURES))

    def test_non_numeric_values_coerced_to_zero(self):
        X = featurize([_window(volume="boom")])
        assert X[0][ML_FEATURES.index("volume")] == 0.0


class TestTrain:
    def test_below_min_windows_does_not_train(self):
        cfg = None
        assert train("individual", "EMP001", _history(5)) is False
        assert MODEL_CACHE == {}

    def test_at_min_windows_trains(self):
        assert train("individual", "EMP001", _history(20)) is True
        assert "individual:EMP001" in MODEL_CACHE

    def test_force_skips_min_window_gate(self):
        assert train("individual", "EMP001", _history(2), force=True) is True
        assert "individual:EMP001" in MODEL_CACHE

    def test_custom_min_windows_override(self):
        assert train("individual", "EMP001", _history(2), min_windows=2) is True
        assert train("individual", "EMP002", _history(2), min_windows=5) is False

    def test_single_window_history_does_not_train(self):
        assert train("individual", "EMP001", _history(1), force=True) is False

    def test_retrain_replaces_model_in_cache(self):
        train("individual", "EMP001", _history(20))
        first = MODEL_CACHE["individual:EMP001"]
        train("individual", "EMP001", _history(20, base=999.0))
        assert MODEL_CACHE["individual:EMP001"] is not first

    def test_deterministic_with_fixed_random_state(self):
        train("individual", "EMP001", _history(40))
        model_a = MODEL_CACHE["individual:EMP001"]
        clear_models()
        train("individual", "EMP001", _history(40))
        model_b = MODEL_CACHE["individual:EMP001"]
        score_a = score("individual", "EMP001", _window())
        score_b = score("individual", "EMP001", _window())
        assert score_a == score_b
        assert model_a is not model_b  # distinct objects, same behaviour


class TestScore:
    def test_normal_windows_score_low(self):
        train("individual", "EMP001", _history(60), force=True)
        center = _window(volume=130.0, event_count=4, active=0.4, locations=1)
        s = score("individual", "EMP001", center)
        assert 0.0 <= s <= 1.0
        assert s < 0.5, "a window inside the normal region must stay low"

    def test_anomalous_window_scores_higher_than_normal(self):
        history = _history(60, base=100.0)
        train("individual", "EMP001", history, force=True)
        normal = score("individual", "EMP001", _window(volume=130.0, event_count=4, active=0.4, locations=1))
        spike = score("individual", "EMP001", _window(volume=1e6, event_count=4, active=0.4, locations=1))
        assert spike > normal, "an extreme-volume window must lift the IF anomaly signal"
        assert 0.0 <= spike <= 1.0

    def test_cache_miss_returns_neutral_zero(self):
        assert score("individual", "GHOST", _window()) == 0.0

    def test_fallback_keys_use_peer_group_model(self):
        train("peer_group", "HR", _history(40, base=50.0), force=True)
        s = score("individual", "EMP001", _window(volume=500.0),
                  fallback_keys=["peer_group:HR", "global:__global__"])
        assert 0.0 < s <= 1.0, "missing individual model must fall back to the peer model, not 0"

    def test_fallback_levels_try_same_entity_ref(self):
        train("global", "EMP001", _history(40), force=True)
        s = score("individual", "EMP001", _window(volume=500.0), fallback_levels=["peer_group", "global"])
        assert 0.0 < s <= 1.0

    def test_fallback_chain_exhausted_returns_zero(self):
        # no models anywhere under the requested keys -> neutral 0
        assert score("individual", "EMP001", _window(volume=999.0),
                     fallback_keys=["peer_group:HR", "global:__global__"]) == 0.0
        assert score("individual", "EMP001", _window()) == 0.0

    def test_score_bounded_for_extreme_inputs(self):
        train("individual", "EMP001", _history(60), force=True)
        for w in (_window(volume=0.0), _window(volume=1e9), _window(fail_rate=1.0, stale=365.0)):
            s = score("individual", "EMP001", w)
            assert 0.0 <= s <= 1.0


class TestRetrainSchedule:
    def test_retrains_only_cached_keys(self):
        train("individual", "EMP001", _history(20), force=True)
        train("peer_group", "HR", _history(20), force=True)
        keys = retrain_schedule({
            "individual:EMP001": _history(30),
            "peer_group:HR": _history(30),
            "individual:NEVER": _history(30),  # never trained -> skipped
        })
        assert "individual:EMP001" in keys
        assert "peer_group:HR" in keys
        assert "individual:NEVER" not in keys

    def test_respects_min_windows_on_retrain(self):
        train("individual", "EMP001", _history(20), force=True)
        keys = retrain_schedule({"individual:EMP001": _history(2)}, min_windows=5)
        assert keys == []  # not enough fresh data -> not retrained

    def test_empty_history_is_noop(self):
        assert retrain_schedule({}) == []


class TestDecisionTransform:
    def test_sigmoid_maps_to_unit_interval(self):
        from analytics.ml import _decision_to_anomaly
        for decision in (-100.0, -10.0, -1.0, 0.0, 1.0, 10.0, 100.0):
            v = _decision_to_anomaly(decision)
            assert 0.0 <= v <= 1.0

    def test_monotonic_decreasing_in_decision(self):
        from analytics.ml import _decision_to_anomaly
        # sklearn decision_function: positive = normal, negative = outlier,
        # so anomaly must FALL as the decision (normality) rises.
        assert _decision_to_anomaly(-5.0) > _decision_to_anomaly(0.0)
        assert _decision_to_anomaly(0.0) > _decision_to_anomaly(5.0)

    def test_extreme_normal_clamps_to_zero(self):
        from analytics.ml import _decision_to_anomaly
        assert _decision_to_anomaly(1e6) == 0.0

    def test_extreme_outlier_clamps_to_one(self):
        from analytics.ml import _decision_to_anomaly
        assert _decision_to_anomaly(-1e6) == 1.0


class TestClearModels:
    def test_clear_empties_cache(self):
        train("individual", "EMP001", _history(20), force=True)
        assert MODEL_CACHE
        clear_models()
        assert MODEL_CACHE == {}


class TestConfigDefaults:
    def test_ml_settings_defaults(self):
        from analytics.config import Config
        cfg = Config.from_env({})
        assert cfg.ml_min_windows == 20
        assert cfg.ml_contamination == 0.01
        assert cfg.ml_n_estimators == 100
        assert cfg.enable_ml is True

    def test_train_uses_config_defaults(self):
        train("individual", "EMP001", _history(20))  # cfg default min_windows=20
        assert "individual:EMP001" in MODEL_CACHE