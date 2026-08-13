"""UEBA Simulator package.

Phase 1 delivers the Common Event Schema (`schema`) and a deterministic
organization simulator (`org`, `engine`, `backfill`, `live`, `anomaly`,
`ground_truth`).
"""

from .schema import build_event, Event  # noqa: F401

__all__ = ["build_event", "Event"]