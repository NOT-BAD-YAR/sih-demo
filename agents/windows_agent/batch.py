"""Batching + flush to Kafka.

Readers emit normalized Events as fast as they observe them; `BatchBuffer`
collects them and flushes as a batch once either the event count (N) or the
wall-clock age (T seconds) threshold is hit. Flush failures retry with
exponential backoff; on persistent failure the batch is retained (never
silently dropped) and an error is surfaced for the service loop.
"""

from __future__ import annotations

import time
from threading import Lock
from typing import Any, Callable, Sequence

from simulator.schema import Event
from streaming.producer import normalize_payload


class BatchFlushError(RuntimeError):
    """Raised when a flush cannot be delivered after all retries."""


class BatchBuffer:
    """Thread-safe buffer that flushes on count or time threshold."""

    def __init__(
        self,
        sender: Callable[[list[dict]], None],
        *,
        max_size: int = 50,
        flush_interval_sec: float = 5.0,
        max_retries: int = 3,
        backoff_base: float = 1.0,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._sender = sender
        self.max_size = max(int(max_size), 1)
        self.flush_interval_sec = float(flush_interval_sec)
        self.max_retries = int(max_retries)
        self.backoff_base = float(backoff_base)
        self._now = now
        self._lock = Lock()
        self._buffer: list[dict] = []
        self._last_flush = now()
        self.sent = 0
        self.flushes = 0
        self.failures = 0

    # -- stats ----------------------------------------------------------------

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "buffered": len(self._buffer),
                "sent": self.sent,
                "flushes": self.flushes,
                "failures": self.failures,
            }

    @property
    def pending(self) -> int:
        with self._lock:
            return len(self._buffer)

    # -- main API -------------------------------------------------------------

    def add(self, event: Event | dict[str, Any]) -> None:
        """Enqueue a normalized event (Event object or wire dict)."""
        payload = normalize_payload(event) if isinstance(event, Event) else dict(event)
        need_flush = False
        with self._lock:
            self._buffer.append(payload)
            need_flush = len(self._buffer) >= self.max_size
        if need_flush:
            self.flush()

    def add_many(self, events: Sequence[Event | dict[str, Any]]) -> None:
        for event in events:
            self.add(event)
        self.flush()

    def tick(self) -> None:
        """Time-based flush check — call from the service loop."""
        if self.pending == 0:
            return
        with self._lock:
            aged = self._now() - self._last_flush >= self.flush_interval_sec
        if aged:
            self.flush()

    def flush(self) -> int:
        """Deliver the buffered batch; returns number of events sent.

        Retries with exponential backoff up to max_retries. If the sender still
        fails, the batch stays buffered and BatchFlushError is raised so the
        service can surface it (data is never dropped).
        """
        with self._lock:
            batch = self._buffer
            self._buffer = []
            self._last_flush = self._now()

        if not batch:
            return 0

        attempt = 0
        while True:
            try:
                self._sender(batch)
                with self._lock:
                    self.sent += len(batch)
                    self.flushes += 1
                return len(batch)
            except Exception:
                attempt += 1
                if attempt > self.max_retries:
                    # return the batch to the buffer (front) so nothing is lost
                    with self._lock:
                        self._buffer = batch + self._buffer
                        self.failures += 1
                    raise BatchFlushError(
                        f"flush failed after {self.max_retries} retries; {len(batch)} events retained"
                    ) from None
                time.sleep(self.backoff_base * (2 ** (attempt - 1)))

    def flush_to(self, sender: Callable[[list[dict]], None]) -> int:
        """Testable helper: flush the current buffer to an arbitrary sender."""
        with self._lock:
            batch = self._buffer
            self._buffer = []
            self._last_flush = self._now()
        if not batch:
            return 0
        sender(batch)
        with self._lock:
            self.sent += len(batch)
            self.flushes += 1
        return len(batch)


def run_batch(
    events: Sequence[Event | dict[str, Any]],
    sender: Callable[[list[dict]], None],
    *,
    max_size: int = 50,
    flush_interval_sec: float = 5.0,
    max_retries: int = 3,
) -> int:
    """Convenience: push a list of events through a BatchBuffer and flush.

    Returns the total number of events delivered to the sender.
    """
    buffer = BatchBuffer(
        sender,
        max_size=max_size,
        flush_interval_sec=flush_interval_sec,
        max_retries=max_retries,
    )
    for event in events:
        buffer.add(event)
    buffer.flush()
    return buffer.stats()["sent"]