"""Pipeline health + lag monitoring.

Used by the engine's `/health` endpoint (Phase 6) and by operations. Reports
topic existence, partitions, consumer lag.
"""

from __future__ import annotations

from confluent_kafka import Consumer, TopicPartition
from confluent_kafka.admin import AdminClient

from .topics import topic_defs


def admin_client(bootstrap: str) -> AdminClient:
    return AdminClient({"bootstrap.servers": bootstrap})


def health(bootstrap: str, topics: list[str] | None = None) -> dict:
    """Return {"kafka": bool, "topics": {name: {exists, partitions}}}."""
    ac = admin_client(bootstrap)
    names = topics or [t.name for t in topic_defs()]
    result: dict = {"kafka": False, "topics": {}}
    try:
        metadata = ac.list_topics(timeout=10)
        result["kafka"] = True
        for name in names:
            topic = metadata.topics.get(name)
            result["topics"][name] = {
                "exists": topic is not None,
                "partitions": topic is not None and len(topic.partitions) or 0,
            }
    except Exception as exc:  # broker unreachable
        result["kafka"] = False
        result["error"] = str(exc)
    return result


def consumer_lag(bootstrap: str, group_id: str, topics: list[str]) -> dict:
    """Per-partition lag for the engine consumer group.

    Assumes topics configured per topics.py (4 partitions). For each partition:
    lag = (latest watermark offset) - (committed offset). Uncommitted => lag = end.
    """
    conf = {
        "bootstrap.servers": bootstrap,
        "group.id": group_id,
        "enable.auto.commit": False,
        "auto.offset.reset": "earliest",
    }
    lag_report: dict = {}
    try:
        consumer = Consumer(conf)
        try:
            tps: list[TopicPartition] = []
            for topic in topics:
                metadata = consumer.list_topics(topic, timeout=10).topics[topic]
                for partition in metadata.partitions.values():
                    tps.append(TopicPartition(topic, partition.id, 0))
            committed = consumer.committed(tps, timeout=10)
            committed_map = {f"{tp.topic}:{tp.partition}": tp.offset for tp in committed}
            for tp in tps:
                end = consumer.get_watermark_offsets(tp, timeout=10)[1]
                cur = committed_map.get(f"{tp.topic}:{tp.partition}")
                # uncommitted partitions report INVALID_OFFSET (-1001) -> treat as 0
                current = cur if cur is not None and cur >= 0 else 0
                lag_report[f"{tp.topic}:{tp.partition}"] = int(max(0, end - current))
        finally:
            consumer.close()
    except Exception as exc:
        lag_report["error"] = str(exc)
    return lag_report