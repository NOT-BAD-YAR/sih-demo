"""Windows Agent — service entrypoint.

Runs the enabled readers on threads, normalizes every raw record into the
Common Event Schema, and flushes batches to Kafka through the shared producer
path. Readers fail open: an unavailable or erroring source disables itself and
the rest of the agent keeps running. `--dry-run` prints normalized events
instead of shipping them (useful to verify collection without Kafka).
"""

from __future__ import annotations

import argparse
import logging
import time
from collections import defaultdict
from typing import Callable

from .batch import BatchBuffer
from .config import AgentConfig
from .normalize import to_schema
from .readers import ReaderRunner, build_readers
from streaming.producer import EventProducer
from streaming.topics import topic_for

log = logging.getLogger("windows_agent")


class KafkaSink:
    """Batch sender: routes normalized wire dicts to their topic and flushes.

    Keeps one idempotent producer per topic. Any delivery failure raises so the
    BatchBuffer can retry with backoff (data retained on failure).
    """

    def __init__(self, bootstrap: str) -> None:
        self.bootstrap = bootstrap
        self._producers: dict[str, EventProducer] = {}
        self.topic_counts: dict[str, int] = defaultdict(int)

    def __call__(self, events: list[dict]) -> None:
        by_topic: dict[str, list[dict]] = defaultdict(list)
        for event in events:
            by_topic[topic_for(event["event_type"])].append(event)
        for topic, batch in by_topic.items():
            producer = self._producers.get(topic)
            if producer is None:
                producer = EventProducer(self.bootstrap, topic)
                self._producers[topic] = producer
            for event in batch:
                producer.send(event)
            remaining = producer.flush(timeout=10)
            if remaining:
                raise RuntimeError(f"topic {topic}: {remaining} messages undelivered")
            self.topic_counts[topic] += len(batch)

    def close(self) -> None:
        for producer in self._producers.values():
            try:
                producer.close()
            except Exception:  # noqa: BLE001
                pass
        self._producers.clear()


class PrintSink:
    """Dry-run sink: emit normalized events to stdout for verification."""

    def __init__(self) -> None:
        self.events: list[dict] = []

    def __call__(self, events: list[dict]) -> None:
        for event in events:
            print(
                f"[agent] {event['ts']} {event['event_type']:>12} "
                f"{event.get('user_id') or event.get('actor')} "
                f"target={event.get('target_entity') or '-'} "
                f"topic={topic_for(event['event_type'])}"
            )
            self.events.extend(events)


def run(config: AgentConfig | None = None, *, dry_run: bool = False,
        once: bool = False, stop_after_sec: float | None = None) -> dict:
    """Run the agent. Returns a status dict (stats + reader states)."""
    cfg = config or AgentConfig.from_env()
    sink: Callable[[list[dict]], None] = PrintSink() if dry_run else KafkaSink(cfg.kafka_bootstrap)
    buffer = BatchBuffer(
        sink,
        max_size=cfg.batch_size,
        flush_interval_sec=cfg.flush_interval_sec,
    )

    def ingest(record: dict) -> None:
        event = to_schema(record, hostname=cfg.hostname)
        if event is not None:
            buffer.add(event)

    readers = build_readers(cfg)
    runner = ReaderRunner(
        readers,
        ingest,
        poll_interval_sec=cfg.poll_interval_sec,
        max_errors=cfg.max_reader_errors,
    )
    runner.start()

    started = time.monotonic()
    try:
        while True:
            buffer.tick()
            if once:
                runner.poll_all()
                break
            if stop_after_sec is not None and time.monotonic() - started >= stop_after_sec:
                break
            time.sleep(0.5)
    finally:
        runner.stop()
        buffer.flush()
        if isinstance(sink, KafkaSink):
            sink.close()

    status = {
        "stats": buffer.stats(),
        "readers": runner.status(),
        "topics": dict(sink.topic_counts) if isinstance(sink, KafkaSink) else {},
    }
    if isinstance(sink, PrintSink):
        status["dry_run_events"] = len(sink.events)
    return status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="windows_agent", description="UEBA Windows endpoint agent")
    parser.add_argument("--config", help="path to agent.toml/json config (overrides defaults)")
    parser.add_argument("--dry-run", action="store_true", help="print normalized events, do not ship")
    parser.add_argument("--once", action="store_true", help="one poll cycle per reader, then exit")
    parser.add_argument("--seconds", type=float, default=None, help="run for N seconds then exit")
    parser.add_argument("--verbose", action="store_true", help="enable debug logging")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    config = AgentConfig.from_file(args.config) if args.config else None
    status = run(config, dry_run=args.dry_run, once=args.once, stop_after_sec=args.seconds)
    for name, state in status["readers"].items():
        flag = "enabled" if state["enabled"] else "disabled"
        print(f"reader {name:>14}: {flag}  events={state['events']} errors={state['errors']} {state['disabled_reason']}")
    print(f"stats: {status['stats']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())