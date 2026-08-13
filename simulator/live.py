"""Real-time scheduler service.

Long-running loop that emits normalized events at the present moment — the
same producer path the Windows agent will use in Phase 8.
"""

from __future__ import annotations

import random
import time
from datetime import datetime, timezone

from .engine import generate_live_events
from .org import Organization
from .schema import Event


def run_live(org: Organization, interval_sec: float = 5.0, max_ticks: int | None = None, sink=None, seed: int = 1) -> list[Event]:
    """Emit live events every `interval_sec` (blocking). Returns events if max_ticks set."""
    rng = random.Random(seed)
    emitted: list[Event] = []
    ticks = 0
    while max_ticks is None or ticks < max_ticks:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        batch = generate_live_events(org, rng, now, k=3)
        if sink is not None:
            sink(batch)
        emitted.extend(batch)
        ticks += 1
        if max_ticks is not None:
            break
        time.sleep(interval_sec)
    return emitted