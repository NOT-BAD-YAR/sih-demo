"""Phase 8 — AgentConfig loading (env + file) and validation."""

import json

import pytest

from agents.windows_agent.config import AgentConfig, ALL_READERS

pytestmark = pytest.mark.unit


class TestFromEnv:
    def test_defaults(self):
        cfg = AgentConfig.from_env({})
        assert set(cfg.enabled_readers) == set(ALL_READERS)
        assert cfg.poll_interval_sec == 5.0
        assert cfg.batch_size == 50
        assert cfg.hostname  # auto-detected

    def test_overrides(self):
        cfg = AgentConfig.from_env(
            {
                "AGENT_READERS": "security_log,file_watcher",
                "AGENT_POLL_INTERVAL_SEC": "2",
                "AGENT_BATCH_SIZE": "10",
                "AGENT_FLUSH_INTERVAL_SEC": "1.5",
                "AGENT_HOSTNAME": "DESKTOP-DEMO",
                "AGENT_MAX_ERRORS": "7",
            }
        )
        assert cfg.enabled_readers == ["security_log", "file_watcher"]
        assert cfg.poll_interval_sec == 2.0
        assert cfg.batch_size == 10
        assert cfg.flush_interval_sec == 1.5
        assert cfg.hostname == "DESKTOP-DEMO"
        assert cfg.max_reader_errors == 7

    def test_kafka_bootstrap_falls_back_to_global(self):
        cfg = AgentConfig.from_env({"KAFKA_BOOTSTRAP": "kafka:9092"})
        assert cfg.kafka_bootstrap == "kafka:9092"

    def test_unknown_reader_rejected(self):
        with pytest.raises(ValueError):
            AgentConfig.from_env({"AGENT_READERS": "registry"})

    def test_reader_enabled_helper(self):
        cfg = AgentConfig(enabled_readers=["security_log"])
        assert cfg.reader_enabled("security_log") is True
        assert cfg.reader_enabled("usb") is False


class TestFromFile:
    def test_json_config(self, tmp_path):
        p = tmp_path / "agent.json"
        p.write_text(
            json.dumps({"readers": ["file_watcher"], "watch_dirs": ["C:\\tmp"], "batch_size": 5}),
            encoding="utf-8",
        )
        cfg = AgentConfig.from_file(p)
        assert cfg.enabled_readers == ["file_watcher"]
        assert cfg.batch_size == 5

    def test_tomlish_config(self, tmp_path):
        p = tmp_path / "agent.toml"
        p.write_text(
            "# agent config\nreaders = security_log,usb\nwatch_dirs = \"C:\\Users\"\nhostname = \"HOST-X\"\n",
            encoding="utf-8",
        )
        cfg = AgentConfig.from_file(p)
        assert cfg.enabled_readers == ["security_log", "usb"]
        assert cfg.hostname == "HOST-X"

    def test_to_dict_roundtrip(self):
        cfg = AgentConfig(enabled_readers=["file_watcher"])
        d = cfg.to_dict()
        assert d["enabled_readers"] == ["file_watcher"]
        assert d["watch_dirs"] and d["hostname"]
        assert "kafka_bootstrap" in d