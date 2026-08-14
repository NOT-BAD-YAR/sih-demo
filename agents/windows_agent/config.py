"""Agent configuration.

Loaded from environment variables (or an optional agent.toml / json file).
Controls which readers run, what the readers watch, and how the batch sender
flushes to Kafka. Fail-open: a misconfigured reader is disabled at startup,
never fatal to the whole agent.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

ALL_READERS = ("security_log", "sysmon", "file_watcher", "usb", "process")


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_list(name: str, default: str) -> list[str]:
    raw = os.getenv(name, default)
    return [p.strip() for p in raw.split(",") if p.strip()]


@dataclass
class AgentConfig:
    """Runtime configuration for the Windows agent."""

    enabled_readers: list[str] = field(default_factory=lambda: list(ALL_READERS))
    watch_dirs: list[str] = field(default_factory=lambda: ["C:\\Users"])
    poll_interval_sec: float = 5.0
    batch_size: int = 50
    flush_interval_sec: float = 5.0
    kafka_bootstrap: str = "localhost:9092"
    hostname: str = ""
    max_reader_errors: int = 5
    security_log_channel: str = "Security"
    sysmon_channel: str = "Microsoft-Windows-Sysmon/Operational"
    file_extension_filter: list[str] = field(
        default_factory=lambda: [".xlsx", ".docx", ".pdf", ".zip", ".csv", ".db"]
    )
    wevtutil_bin: str = "wevtutil"
    powershell_bin: str = "powershell"

    def __post_init__(self) -> None:
        unknown = set(self.enabled_readers) - set(ALL_READERS)
        if unknown:
            raise ValueError(f"unknown readers in config: {sorted(unknown)}")
        if not self.hostname:
            self.hostname = os.getenv("COMPUTERNAME") or os.getenv("HOSTNAME") or "AGENT-01"
        self.hostname = self.hostname.upper()

    # -- field lookup helpers -------------------------------------------------

    def reader_enabled(self, name: str) -> bool:
        return name in self.enabled_readers

    def entity_id_for(self, event_type: str) -> str:
        """Map a Common Schema event_type to the agent's device entity id."""
        return f"{self.hostname}"

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "AgentConfig":
        """Build config from os.environ (or a provided dict for tests)."""
        old = os.environ
        try:
            if env is not None:
                os.environ = {**old, **{k: str(v) for k, v in env.items()}}
            readers = _env_list("AGENT_READERS", ",".join(ALL_READERS))
            watch = _env_list("AGENT_WATCH_DIRS", "C:\\Users")
            exts = _env_list("AGENT_FILE_EXTS", ".xlsx,.docx,.pdf,.zip,.csv,.db")
            return AgentConfig(
                enabled_readers=readers,
                watch_dirs=watch,
                poll_interval_sec=float(os.getenv("AGENT_POLL_INTERVAL_SEC", "5.0")),
                batch_size=int(os.getenv("AGENT_BATCH_SIZE", "50")),
                flush_interval_sec=float(os.getenv("AGENT_FLUSH_INTERVAL_SEC", "5.0")),
                kafka_bootstrap=os.getenv("AGENT_KAFKA_BOOTSTRAP", os.getenv("KAFKA_BOOTSTRAP", "localhost:9092")),
                hostname=os.getenv("AGENT_HOSTNAME", ""),
                max_reader_errors=int(os.getenv("AGENT_MAX_ERRORS", "5")),
                security_log_channel=os.getenv("AGENT_SECURITY_CHANNEL", "Security"),
                sysmon_channel=os.getenv("AGENT_SYSMON_CHANNEL", "Microsoft-Windows-Sysmon/Operational"),
                file_extension_filter=exts,
                wevtutil_bin=os.getenv("AGENT_WEVTUTIL", "wevtutil"),
                powershell_bin=os.getenv("AGENT_POWERSHELL", "powershell"),
            )
        finally:
            os.environ = old

    @classmethod
    def from_file(cls, path: str | Path) -> "AgentConfig":
        """Load from a JSON or .toml-ish (key=value) config file, then env overrides."""
        p = Path(path)
        data: dict[str, object] = {}
        if p.suffix.lower() == ".json":
            data = json.loads(p.read_text(encoding="utf-8"))
        else:
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                data[k.strip()] = v.strip().strip('"')
        cfg = cls.from_env()
        if "readers" in data:
            cfg.enabled_readers = _to_list(data["readers"])
        if "watch_dirs" in data:
            cfg.watch_dirs = _to_list(data["watch_dirs"])
        if "poll_interval_sec" in data:
            cfg.poll_interval_sec = float(data["poll_interval_sec"])
        if "batch_size" in data:
            cfg.batch_size = int(data["batch_size"])
        if "flush_interval_sec" in data:
            cfg.flush_interval_sec = float(data["flush_interval_sec"])
        if "kafka_bootstrap" in data:
            cfg.kafka_bootstrap = str(data["kafka_bootstrap"])
        if "hostname" in data:
            cfg.hostname = str(data["hostname"])
        if "max_reader_errors" in data:
            cfg.max_reader_errors = int(data["max_reader_errors"])
        return cfg

    def to_dict(self) -> dict[str, object]:
        return {
            "enabled_readers": list(self.enabled_readers),
            "watch_dirs": list(self.watch_dirs),
            "poll_interval_sec": self.poll_interval_sec,
            "batch_size": self.batch_size,
            "flush_interval_sec": self.flush_interval_sec,
            "kafka_bootstrap": self.kafka_bootstrap,
            "hostname": self.hostname,
            "max_reader_errors": self.max_reader_errors,
            "file_extension_filter": list(self.file_extension_filter),
        }


def _to_list(value: object) -> list[str]:
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    return []