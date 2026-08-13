"""90-day historical backfill.

Fast bulk path that produces normal history for baseline warm-up. Writes to a
callable sink (`out_fn`) so both file and Kafka-style consumers are easy.
"""

from __future__ import annotations

from typing import Callable, Iterable

from .engine import run_backfill as _run_backfill
from .org import Organization
from .schema import Event

EventSink = Callable[[Iterable[Event]], None]


def run_backfill(org: Organization, days: int = 90, events_per_day: int = 12, seed: int = 42, sink: EventSink | None = None) -> list[Event]:
    """Generate `days` of normal history; optionally stream into `sink`."""
    events = _run_backfill(org, days=days, events_per_day=events_per_day, seed=seed)
    if sink is not None:
        sink(events)
    return events


def jsonl_sink(path: str) -> EventSink:
    """Sink factory: write events as newline-delimited JSON."""

    def _sink(events: Iterable[Event]) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            for ev in events:
                fh.write(ev.to_json + "\n")

    return _sink