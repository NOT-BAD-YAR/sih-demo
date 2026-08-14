"""Process reader (tasklist snapshot diff — best-effort).

Windows doesn't expose process create/terminate to a normal user without ETW
or Sysmon; this reader takes a lightweight snapshot of `tasklist` each poll and
diffs it, emitting `started` / `stopped` raw records. It is explicitly
best-effort: if `tasklist` is unavailable the reader is skipped and the rest of
the agent continues.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime, timezone

from ..config import AgentConfig
from ._winutil import run_capture
from .base import Reader


class ProcessReader(Reader):
    name = "process"

    def __init__(self, config: AgentConfig) -> None:
        super().__init__(config)
        self._snapshot: dict[int, str] = {}

    def available(self) -> bool:
        return run_capture(["tasklist", "/FO", "CSV", "/NH"]) is not None

    def poll_once(self) -> list[dict]:
        text = run_capture(["tasklist", "/FO", "CSV", "/NH"])
        if text is None:
            raise RuntimeError("tasklist failed")
        current = _parse_csv(text)
        now = datetime.now(timezone.utc)

        records: list[dict] = []
        for pid, image in current.items():
            if pid not in self._snapshot:
                records.append(_record(image, pid, "started", now))
        for pid, image in self._snapshot.items():
            if pid not in current:
                records.append(_record(image, pid, "stopped", now))

        self._snapshot = current
        return records


def _parse_csv(text: str) -> dict[int, str]:
    out: dict[int, str] = {}
    try:
        reader = csv.reader(io.StringIO(text))
        for row in reader:
            if len(row) < 2:
                continue
            image, pid = row[0].strip(), row[1].strip()
            if image in ("", "Image Name"):
                continue
            try:
                out[int(pid)] = image
            except ValueError:
                continue
    except Exception:
        return {}
    return out


def _record(image: str, pid: int, action: str, now: datetime) -> dict:
    return {
        "source": "process",
        "ts": now.isoformat(),
        "process": image,
        "pid": pid,
        "action": action,
        "user": "",
    }