"""Reader registry — one factory for all Phase 8 readers."""

from __future__ import annotations

from ..config import AgentConfig, ALL_READERS
from .base import Reader, ReaderRunner
from .file_watcher import FileWatcherReader
from .process import ProcessReader
from .security_log import SecurityLogReader
from .sysmon import SysmonReader
from .usb import UsbReader

READERS: dict[str, type[Reader]] = {
    "security_log": SecurityLogReader,
    "sysmon": SysmonReader,
    "file_watcher": FileWatcherReader,
    "usb": UsbReader,
    "process": ProcessReader,
}


def build_readers(config: AgentConfig) -> list[Reader]:
    """Instantiate the enabled readers (startup order preserved from ALL_READERS)."""
    return [READERS[name](config) for name in ALL_READERS if config.reader_enabled(name)]


__all__ = [
    "Reader",
    "ReaderRunner",
    "build_readers",
    "READERS",
    "ALL_READERS",
]