"""UEBA analytics package.

Phase 0 delivers `config.py` (central configuration). Phase 4A adds the
event processor, feature engine, and three-level baseline engine. Phase 4B
adds the deterministic rule detectors under `analytics/rules/`. ML, context,
risk, and correlation modules arrive in Phases 4C–4E.
"""

from analytics.config import Config

__all__ = ["Config"]