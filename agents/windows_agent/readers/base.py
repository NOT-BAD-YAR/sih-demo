"""Reader base class + supervision.

Each `Reader` is a poller: `poll_once()` returns raw records observed since
the last poll. A reader may be *unavailable* (no permission, channel missing,
binary missing) — it reports `available() == False` and is disabled at startup.
`ReaderRunner` supervises one or many readers: repeated failures disable a
reader gracefully (log once, keep every other reader running) — the fail-open
design keeps the agent alive even when one source misbehaves.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict
from typing import Callable

from ..config import AgentConfig

log = logging.getLogger("windows_agent")


class Reader:
    """Base poller. Subclasses implement `poll_once` and may override `available`."""

    name: str = "reader"

    def __init__(self, config: AgentConfig) -> None:
        self.config = config
        self.events_total = 0

    def available(self) -> bool:
        """Prerequisite probe; False → reader disabled at startup (fail-open)."""
        return True

    def poll_once(self) -> list[dict]:
        """Return raw records observed since the last poll (empty = nothing new)."""
        raise NotImplementedError


class ReaderRunner:
    """Runs readers on threads, disables a reader after repeated consecutive errors."""

    def __init__(
        self,
        readers: list[Reader],
        sink: Callable[[dict], None],
        *,
        poll_interval_sec: float = 5.0,
        max_errors: int = 5,
    ) -> None:
        self._readers = {r.name: r for r in readers}
        self._sink = sink
        self.poll_interval_sec = float(poll_interval_sec)
        self.max_errors = int(max_errors)
        self._errors: dict[str, int] = defaultdict(int)
        self._events: dict[str, int] = defaultdict(int)
        self._enabled = {r.name: r.available() for r in readers}
        self._disabled_reason: dict[str, str] = {}
        self._started = False
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._lock = threading.Lock()

    # -- introspection --------------------------------------------------------

    def status(self) -> dict[str, object]:
        with self._lock:
            return {
                name: {
                    "enabled": self._enabled.get(name, False),
                    "events": self._events.get(name, 0),
                    "errors": self._errors.get(name, 0),
                    "disabled_reason": self._disabled_reason.get(name, ""),
                }
                for name in self._readers
            }

    def enabled_count(self) -> int:
        return sum(1 for v in self._enabled.values() if v)

    # -- supervision ----------------------------------------------------------

    def _poll_one(self, name: str) -> None:
        """Supervise a single reader poll; disable it on persistent failure."""
        reader = self._readers[name]
        try:
            records = reader.poll_once()
        except Exception as exc:  # noqa: BLE001 - any failure must not kill the agent
            self._errors[name] += 1
            if self._errors[name] >= self.max_errors:
                with self._lock:
                    self._enabled[name] = False
                    self._disabled_reason[name] = (
                        f"disabled after {self._errors[name]} consecutive errors: {exc}"
                    )
                log.error("reader %s disabled: %s", name, exc)
            return
        # success resets the consecutive-error counter
        self._errors[name] = 0
        if records:
            self._events[name] += len(records)
            for record in records:
                self._sink(record)

    def poll_all(self) -> None:
        """One supervision cycle across all enabled readers (synchronous)."""
        for name in list(self._enabled):
            if self._enabled.get(name):
                self._poll_one(name)

    # -- lifecycle ------------------------------------------------------------

    def start(self) -> "ReaderRunner":
        if self._started:
            return self
        self._started = True
        for name, reader in self._readers.items():
            if not self._enabled[name]:
                reason = self._disabled_reason.get(name) or "unavailable at startup"
                self._disabled_reason[name] = reason
                log.warning("reader %s not started: %s", name, reason)
                continue
            thread = threading.Thread(
                target=self._loop,
                args=(name,),
                name=f"reader-{name}",
                daemon=True,
            )
            thread.start()
            self._threads.append(thread)
        return self

    def _loop(self, name: str) -> None:
        while not self._stop.is_set():
            if self._enabled.get(name):
                self._poll_one(name)
            else:
                break
            time.sleep(self.poll_interval_sec)

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        for thread in self._threads:
            thread.join(timeout=timeout)
        self._threads.clear()
        self._started = False