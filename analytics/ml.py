"""ML Engine — 4.4 Isolation Forest anomaly detection (complementary signal).

Trains an unsupervised `sklearn.ensemble.IsolationForest` per level+entity
(individual / peer-group / global), featurizes closed windows into a float
matrix, and returns a 0-1 `score` per window. The output is a **supplementary
signal** fused with the deterministic rules — it is never the sole "malicious"
judge (plan.md §6.2, LLD §4.4).

Cold start: sparse entities fall back to the peer-group model, then the global
model, then a neutral 0 (no signal). Per-entity models only train once they
have enough windows (`ML_MIN_WINDOWS`, default 20).
"""

from __future__ import annotations

import math
from typing import Callable, Optional

import numpy as np
from sklearn.ensemble import IsolationForest

from .config import Config

# feature columns (order defines the featurized matrix; keep stable)
ML_FEATURES = (
    "volume",
    "event_count",
    "active_hours_frac",
    "location_count",
    "location_dist_km",
    "fail_rate",
    "staleness_days",
)

MODEL_CACHE: dict[str, IsolationForest] = {}


class ModelNotReady(KeyError):
    """Raised when no trained model exists for a scoring key."""


def _key(level: str, entity_ref: str) -> str:
    return f"{level}:{entity_ref}"


def _as_float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def featurize(windows: list[dict]) -> np.ndarray:
    """Closed window vectors → float feature matrix (rows = windows).

    Only numeric, scaled-friendly features are used so the Isolation Forest
    operates on a stable, bounded input space. Missing features default to 0.
    """
    rows: list[list[float]] = []
    for w in windows:
        rows.append([_as_float(w.get(f, 0.0)) for f in ML_FEATURES])
    if not rows:
        return np.zeros((0, len(ML_FEATURES)), dtype=float)
    return np.asarray(rows, dtype=float)


def train(
    level: str,
    entity_ref: str,
    history_windows: list[dict],
    *,
    min_windows: int | None = None,
    contamination: float | None = None,
    n_estimators: int | None = None,
    random_state: int = 42,
    force: bool = False,
) -> bool:
    """Train (or retrain) the IsolationForest for a level+entity key.

    Returns True when a model was trained, False when there is not enough
    history yet. `force=True` skips the minimum-window gate (used by tests and
    the global/peer levels which aggregate many members).
    """
    cfg = Config.from_env()
    min_windows = min_windows if min_windows is not None else cfg.ml_min_windows
    contamination = contamination if contamination is not None else cfg.ml_contamination
    n_estimators = n_estimators if n_estimators is not None else cfg.ml_n_estimators

    if not force and len(history_windows) < min_windows:
        return False

    X = featurize(history_windows)
    if X.shape[0] < 2 or X.shape[1] == 0:
        return False

    model = IsolationForest(
        n_estimators=n_estimators,
        contamination=contamination,
        random_state=random_state,
    ).fit(X)

    MODEL_CACHE[_key(level, entity_ref)] = model
    return True


def _decision_to_anomaly(decision: float) -> float:
    """IsolationForest.decision_function value → 0-1 anomaly.

    `decision_function` returns *positive* scores for normal windows and
    *negative* for outliers (sklearn semantics), so the sigmoid must decrease
    in `decision`. A temperature scaled to the LLD's `z → sigmoid → 0-1`
    intent steepens the slope; extreme inputs are clamped to avoid
    `math.exp` overflow.
    """
    z = decision * 8.0
    if z > 700:
        return 0.0
    if z < -700:
        return 1.0
    return 1.0 / (1.0 + math.exp(z))


def score(
    level: str,
    entity_ref: str,
    window: dict,
    *,
    fallback_levels: list[str] | None = None,
    fallback_keys: list[str] | None = None,
) -> float:
    """0-1 anomaly score for one window at `level` for `entity_ref`.

    Fallback chain when the primary model is missing:
      1. `fallback_levels` — try the same entity at another level, e.g.
         `["peer_group", "global"]` (only useful if peer/global models are
         stored under the entity's own ref);
      2. `fallback_keys` — try explicit cache keys, e.g.
         `["peer_group:HR", "global:__global__"]` (how a runner maps an
         entity to its peer-group model by group name);
      3. neutral 0.0 (no signal).
    """
    model = MODEL_CACHE.get(_key(level, entity_ref))
    if model is None:
        for fb in (fallback_levels or []):
            model = MODEL_CACHE.get(_key(fb, entity_ref))
            if model is not None:
                break
    if model is None:
        for key in (fallback_keys or []):
            model = MODEL_CACHE.get(key)
            if model is not None:
                break
    if model is None:
        return 0.0

    X = featurize([window])
    decision = float(model.decision_function(X)[0])
    return round(_decision_to_anomaly(decision), 6)


def retrain_schedule(
    history: dict[str, list[dict]],
    *,
    min_windows: int | None = None,
) -> list[str]:
    """Daily rolling retrain: rebuild every trained level+entity model.

    `history` maps `"{level}:{entity_ref}"` → recent windows. Keys already in
    the cache are retrained with the same gate; returns the retrained keys.
    """
    retrained: list[str] = []
    for key, windows in history.items():
        level, entity_ref = key.split(":", 1)
        if _key(level, entity_ref) not in MODEL_CACHE:
            continue
        if train(level, entity_ref, windows, min_windows=min_windows):
            retrained.append(key)
    return retrained


def clear_models() -> None:
    """Drop all cached models (tests / cold restarts)."""
    MODEL_CACHE.clear()


__all__ = [
    "MODEL_CACHE",
    "ML_FEATURES",
    "featurize",
    "train",
    "score",
    "retrain_schedule",
    "clear_models",
]