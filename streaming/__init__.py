"""UEBA streaming package (Phase 2 — Kafka pipeline).

Modules: topics (partition config), producer (ship events), consumer (engine
ingest worker), monitor (health + lag).
"""

from .topics import topic_for, partition_key, TOPICS  # noqa: F401

__all__ = ["topic_for", "partition_key", "TOPICS"]