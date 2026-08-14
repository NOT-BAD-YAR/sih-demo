"""Phase 8 — agent package structure + API surface (no external services)."""

import pytest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent

EXPECTED_FILES = [
    "agents/__init__.py",
    "agents/windows_agent/__init__.py",
    "agents/windows_agent/config.py",
    "agents/windows_agent/normalize.py",
    "agents/windows_agent/batch.py",
    "agents/windows_agent/main.py",
    "agents/windows_agent/readers/__init__.py",
    "agents/windows_agent/readers/base.py",
    "agents/windows_agent/readers/_winutil.py",
    "agents/windows_agent/readers/security_log.py",
    "agents/windows_agent/readers/sysmon.py",
    "agents/windows_agent/readers/file_watcher.py",
    "agents/windows_agent/readers/usb.py",
    "agents/windows_agent/readers/process.py",
]


@pytest.mark.structure
class TestAgentPackage:
    @pytest.mark.parametrize("rel", EXPECTED_FILES)
    def test_file_exists(self, rel):
        assert (ROOT / rel).exists(), f"missing {rel}"

    def test_modules_importable(self):
        import agents.windows_agent  # noqa: F401
        import agents.windows_agent.config  # noqa: F401
        import agents.windows_agent.normalize  # noqa: F401
        import agents.windows_agent.batch  # noqa: F401
        import agents.windows_agent.main  # noqa: F401
        import agents.windows_agent.readers  # noqa: F401
        import agents.windows_agent.readers.security_log  # noqa: F401
        import agents.windows_agent.readers.sysmon  # noqa: F401
        import agents.windows_agent.readers.file_watcher  # noqa: F401
        import agents.windows_agent.readers.usb  # noqa: F401
        import agents.windows_agent.readers.process  # noqa: F401

    def test_normalize_exposes_to_schema(self):
        from agents.windows_agent.normalize import to_schema
        assert callable(to_schema)

    def test_batch_exposes_buffer_and_run_batch(self):
        from agents.windows_agent.batch import BatchBuffer, BatchFlushError, run_batch
        assert hasattr(BatchBuffer, "add")
        assert hasattr(BatchBuffer, "flush")
        assert hasattr(BatchBuffer, "tick")
        assert callable(run_batch)

    def test_readers_registry_covers_five_sources(self):
        from agents.windows_agent.readers import build_readers
        from agents.windows_agent.readers import READERS
        assert {"security_log", "sysmon", "file_watcher", "usb", "process"} <= set(READERS)
        assert callable(build_readers)

    def test_reader_runner_api_surface(self):
        from agents.windows_agent.readers import ReaderRunner
        for method in ("start", "stop", "poll_all", "status", "enabled_count"):
            assert hasattr(ReaderRunner, method), f"ReaderRunner lacks {method}"

    def test_main_exposes_run_and_sinks(self):
        from agents.windows_agent.main import run, KafkaSink, PrintSink, main
        assert callable(run) and callable(main)
        assert hasattr(KafkaSink, "__call__")
        assert hasattr(PrintSink, "__call__")

    def test_env_example_has_agent_keys(self):
        text = (ROOT / ".env.example").read_text(encoding="utf-8")
        for key in (
            "AGENT_READERS",
            "AGENT_WATCH_DIRS",
            "AGENT_POLL_INTERVAL_SEC",
            "AGENT_BATCH_SIZE",
            "AGENT_FLUSH_INTERVAL_SEC",
            "AGENT_KAFKA_BOOTSTRAP",
        ):
            assert key in text, f"missing .env.example key: {key}"