"""Topic provisioning — create the UEBA topics if they don't exist.

Idempotent: safe to run on every boot. Uses partitions from topics.py.
"""

from __future__ import annotations

import logging

from confluent_kafka.admin import AdminClient, NewTopic, KafkaException

log = logging.getLogger(__name__)


def ensure_topics(bootstrap: str, force_partitions: int = -1) -> dict[str, str]:
    """Create missing topics (or verify existing match expected partitions)."""
    from .topics import topic_defs

    ac = AdminClient({"bootstrap.servers": bootstrap})

    existing = {}
    for meta in ac.list_topics(timeout=15).topics.values():
        existing[meta.topic] = len(meta.partitions)

    results: dict[str, str] = {}
    new_topics = [t for t in topic_defs() if t.name not in existing]

    if new_topics:
        requests = [
            NewTopic(
                t.name,
                num_partitions=t.partitions if force_partitions < 0 else force_partitions,
                replication_factor=t.replication,
            )
            for t in new_topics
        ]
        futures = ac.create_topics(requests)
        for name, fut in futures.items():
            try:
                fut.result()
                results[name] = "created"
                log.info("created topic %s", name)
            except KafkaException as exc:
                results[name] = f"ERROR: {exc}"
                log.error("failed to create %s: %s", name, exc)
    else:
        for t in topic_defs():
            results[t.name] = "exists"

    return results