"""Structure verification — the repo scaffolding from Phase 0.

Every folder/file that later phases depend on must exist with the expected
shape. These tests have zero external dependencies (no docker, no DB).
"""

import pytest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent


EXPECTED_TOP_LEVEL = [
    "agents",       # Phase 8
    "simulator",    # Phase 1
    "streaming",    # Phase 2
    "analytics",    # Phases 4A-4E
    "api",          # Phase 6
    "dashboard",    # Phase 7
    "db",           # Phase 3
    "tests",        # all phases
    "docs",         # design docs
    "scripts",      # dev task runner
    "docker-compose.yml",
    ".env.example",
    "Makefile",
    "pytest.ini",
]

EXPECTED_DB_LAYOUT = [
    "db/alembic.ini",
    "db/alembic/env.py",
    "db/alembic/script.py.mako",
    "db/alembic/versions/.gitkeep",
    "db/alembic/versions",
]

EXPECTED_TESTS_LAYOUT = ["unit", "contract", "integration", "manual", "eval"]


@pytest.mark.structure
class TestRepoScaffolding:
    @pytest.mark.parametrize("name", EXPECTED_TOP_LEVEL)
    def test_top_level_exists(self, name):
        assert (ROOT / name).exists(), f"missing: {name}"

    @pytest.mark.parametrize("rel", EXPECTED_DB_LAYOUT)
    def test_db_layout(self, rel):
        assert (ROOT / rel).exists(), f"missing: {rel}"

    @pytest.mark.parametrize("rel", EXPECTED_TESTS_LAYOUT)
    def test_tests_layout(self, rel):
        assert (ROOT / "tests" / rel).is_dir(), f"missing dir: tests/{rel}"


@pytest.mark.structure
class TestAnalyticsPackage:
    def test_config_module_importable(self):
        import analytics
        import analytics.config  # noqa: F401

    def test_package_exports_config(self):
        from analytics import Config
        assert Config is not None

    def test_phase4a_modules_present(self):
        for name in ("processor.py", "features.py", "baseline.py"):
            assert (ROOT / "analytics" / name).exists(), f"missing analytics/{name}"

    def test_phase4a_modules_importable(self):
        import analytics.processor  # noqa: F401
        import analytics.features  # noqa: F401
        import analytics.baseline  # noqa: F401

    def test_processor_exposes_pipeline_api(self):
        from analytics.processor import validate, resolve_user, NormalizedEvent
        assert callable(validate)
        assert callable(resolve_user)

    def test_features_exposes_accumulate_finalize(self):
        from analytics.features import accumulate, finalize, hour_bucket
        assert callable(accumulate)
        assert callable(finalize)
        assert callable(hour_bucket)

    def test_baseline_exposes_confidence_cold_start(self):
        from analytics.baseline import confidence_for, select_level, build_individual
        assert callable(confidence_for)
        assert callable(select_level)
        assert callable(build_individual)


@pytest.mark.structure
class TestComposeFile:
    def test_compose_has_required_services(self):
        import yaml

        text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        data = yaml.safe_load(text)
        services = set(data["services"].keys())
        assert {"postgres", "kafka"} <= services, f"got services: {services}"

    def test_compose_defines_volumes(self):
        import yaml

        text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        data = yaml.safe_load(text)
        assert {"postgres_data"} <= set(data.get("volumes", {}))

    def test_compose_has_healthchecks(self):
        import yaml

        text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        data = yaml.safe_load(text)
        for svc in ("postgres", "kafka"):
            assert "healthcheck" in data["services"][svc], f"{svc} lacks healthcheck"


@pytest.mark.structure
class TestStreamingPackage:
    def test_modules_importable(self):
        import streaming.topics  # noqa: F401
        import streaming.producer  # noqa: F401
        import streaming.consumer  # noqa: F401
        import streaming.monitor  # noqa: F401
        import streaming.admin  # noqa: F401
        import streaming.dedupe  # noqa: F401

    def test_phase2_files_present(self):
        for name in ("topics.py", "producer.py", "consumer.py", "monitor.py", "admin.py", "dedupe.py", "__init__.py"):
            assert (ROOT / "streaming" / name).exists(), f"missing streaming/{name}"

    def test_producer_ships_single_path(self):
        from streaming.producer import EventProducer, normalize_payload

        assert hasattr(EventProducer, "send")
        assert hasattr(EventProducer, "flush")
        assert hasattr(EventProducer, "close")
        assert callable(normalize_payload)

    def test_consumer_exposes_engine_api(self):
        from streaming.consumer import EngineConsumer

        for method in ("poll_once", "run", "close"):
            assert hasattr(EngineConsumer, method), f"EngineConsumer lacks {method}"

    def test_package_exports_topic_helpers(self):
        import streaming

        assert streaming.topic_for and streaming.partition_key


@pytest.mark.structure
class TestEnvExample:
    def test_env_example_has_required_keys(self):
        text = (ROOT / ".env.example").read_text(encoding="utf-8")
        for key in ("POSTGRES_DSN", "KAFKA_BOOTSTRAP", "KAFKA_GROUP_ID", "RULE_VOLUME_THRESHOLD_K"):
            assert key in text, f"missing .env.example key: {key}"