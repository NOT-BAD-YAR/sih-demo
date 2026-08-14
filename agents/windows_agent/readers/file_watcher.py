"""File watcher reader (dependency-free polling scan).

Instead of requiring watchdog/ReadDirectoryChangesW, this reader snapshots the
configured directories on each poll and diffs against the previous snapshot —
producing `created` / `modified` / `deleted` raw records for files matching the
configured extension filter. watchdog can replace the scan later without
changing the reader contract.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from ..config import AgentConfig
from .base import Reader


class FileWatcherReader(Reader):
    name = "file_watcher"

    def __init__(self, config: AgentConfig) -> None:
        super().__init__(config)
        self._snapshot: dict[str, tuple[float, int]] = {}
        self._seen_dirs: list[str] = []

    def available(self) -> bool:
        # at least one configured directory exists → the reader has work to do
        return any(Path(d).is_dir() for d in self.config.watch_dirs)

    def poll_once(self) -> list[dict]:
        current: dict[str, tuple[float, int]] = {}
        for base in self.config.watch_dirs:
            base_path = Path(base)
            if not base_path.is_dir():
                continue
            for root, _dirs, files in os.walk(base_path):
                for name in files:
                    path = Path(root) / name
                    if path.suffix.lower() not in self.config.file_extension_filter:
                        continue
                    try:
                        stat = path.stat()
                        current[str(path)] = (stat.st_mtime, stat.st_size)
                    except OSError:
                        continue

        records: list[dict] = []
        now = datetime.now(timezone.utc)
        for path, (mtime, size) in current.items():
            prev = self._snapshot.get(path)
            if prev is None:
                records.append(_record(path, "created", size, mtime, now))
            elif prev != (mtime, size):
                records.append(_record(path, "modified", size, mtime, now))
        for path in list(self._snapshot):
            if path not in current:
                records.append(_record(path, "deleted", 0, 0.0, now))

        self._snapshot = current
        return records


def _record(path: str, action: str, size: int, mtime: float, now: datetime) -> dict:
    return {
        "source": "file_watcher",
        "ts": now.isoformat(),
        "path": path,
        "size": size,
        "action": action,
        "user": "",  # resolved by the OS session when a watcher is available
        "hostname": "",
    }