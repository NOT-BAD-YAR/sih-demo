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

    def test_phase4b_rules_package_present(self):
        for name in ("__init__.py", "volume_spike.py", "impossible_travel.py",
                     "out_of_scope.py", "dormant.py", "novel_peer.py"):
            assert (ROOT / "analytics" / "rules" / name).exists(), f"missing analytics/rules/{name}"

    def test_phase4b_rules_importable(self):
        import analytics.rules  # noqa: F401
        import analytics.rules.volume_spike  # noqa: F401
        import analytics.rules.impossible_travel  # noqa: F401
        import analytics.rules.out_of_scope  # noqa: F401
        import analytics.rules.dormant  # noqa: F401
        import analytics.rules.novel_peer  # noqa: F401

    def test_rules_registry_covers_five_canonical_cases(self):
        from analytics.rules import rule_names
        assert {"volume_spike", "impossible_travel", "out_of_scope", "dormant", "novel_peer"} <= set(rule_names())

    def test_rule_result_shape(self):
        from analytics.rules import RuleResult
        r = RuleResult(rule="x", triggered=True, severity=0.5, explanation="why", evidence=["e1"])
        assert r.rule == "x" and r.triggered and r.explanation and r.evidence == ["e1"]

    def test_phase4c_ml_module_present(self):
        assert (ROOT / "analytics" / "ml.py").exists(), "missing analytics/ml.py"

    def test_phase4c_ml_importable(self):
        import analytics.ml  # noqa: F401

    def test_ml_exposes_engine_api(self):
        from analytics.ml import featurize, train, score, retrain_schedule, clear_models, ML_FEATURES
        assert callable(featurize)
        assert callable(train)
        assert callable(score)
        assert callable(retrain_schedule)
        assert callable(clear_models)
        assert len(ML_FEATURES) >= 7

    def test_ml_feature_columns_match_baseline_numeric_features(self):
        from analytics.ml import ML_FEATURES
        from analytics.baseline import NUMERIC_FEATURES
        assert set(ML_FEATURES) == set(NUMERIC_FEATURES)

    def test_phase4d_context_risk_modules_present(self):
        for name in ("context.py", "risk.py"):
            assert (ROOT / "analytics" / name).exists(), f"missing analytics/{name}"

    def test_phase4d_modules_importable(self):
        import analytics.context  # noqa: F401
        import analytics.risk  # noqa: F401

    def test_context_exposes_build_and_vector(self):
        from analytics.context import build, ContextVector
        assert callable(build)
        fields = ContextVector.__dataclass_fields__
        assert {"who", "baseline_confidence", "dept_factor"} <= set(fields)

    def test_risk_exposes_compute_fuse_band(self):
        from analytics.risk import compute, fuse, impact, band_of, Risk
        assert callable(compute)
        assert callable(fuse)
        assert callable(impact)
        assert callable(band_of)
        assert hasattr(Risk, "breakdown")

    def test_risk_result_carries_breakdown(self):
        from analytics.risk import compute
        r = compute(anomaly=0.5, impact=0.5, confidence=0.7)
        b = r.breakdown
        assert b["risk"] == r.risk_100 and b["band"] == r.band
        assert set(b) >= {"anomaly", "impact", "confidence", "components"}

    def test_phase4e_correlation_module_present(self):
        assert (ROOT / "analytics" / "correlation.py").exists(), "missing analytics/correlation.py"

    def test_phase4e_correlation_importable(self):
        import analytics.correlation  # noqa: F401

    def test_correlation_exposes_cluster_api(self):
        from analytics.correlation import resolve_chain, cluster_for_entity, maintain_incident, score_event, ScoredEvent, Incident
        assert callable(resolve_chain)
        assert callable(cluster_for_entity)
        assert callable(maintain_incident)
        assert callable(score_event)
        fields = ScoredEvent.__dataclass_fields__
        assert {"event_id", "entity_ref", "ts", "risk", "chain"} <= set(fields)
        inc_fields = Incident.__dataclass_fields__
        assert {"entity_chain", "related_alert_ids", "evidence_refs", "status"} <= set(inc_fields)

    def test_incident_serializes_to_row(self):
        from analytics.correlation import Incident
        inc = Incident(entity_ref="u1", risk=80, severity="Critical")
        row = inc.row()
        assert row["entity_ref"] == "u1"
        assert set(row) >= {"entity_chain", "related_alert_ids", "evidence_refs", "notes", "status"}

    def test_phase4f_runner_module_present(self):
        assert (ROOT / "analytics" / "runner.py").exists(), "missing analytics/runner.py"

    def test_phase4f_runner_importable(self):
        import analytics.runner  # noqa: F401

    def test_runner_exposes_orchestrator_api(self):
        from analytics.runner import AnalyticsRunner, cron, ML_ONLY_THRESHOLD
        assert callable(AnalyticsRunner)
        assert callable(cron)
        assert 0.0 < ML_ONLY_THRESHOLD < 1.0
        for method in ("on_event", "flush", "run"):
            assert callable(getattr(AnalyticsRunner, method))

    def test_runner_init_accepts_history_baseline(self):
        from analytics.runner import AnalyticsRunner
        runner = AnalyticsRunner(history={"u1": [{"window_start": "2026-01-01T10:00:00+00:00", "volume": 1}]})
        assert runner._history["u1"]

    def test_runner_stats_surface(self):
        from analytics.runner import AnalyticsRunner
        runner = AnalyticsRunner()
        assert set(runner.stats) >= {
            "events", "dropped", "windows_closed", "alerts", "incidents", "escalations",
        }


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

    def test_env_example_has_ml_keys(self):
        text = (ROOT / ".env.example").read_text(encoding="utf-8")
        for key in ("ML_MIN_WINDOWS", "ML_CONTAMINATION", "ML_N_ESTIMATORS"):
            assert key in text, f"missing .env.example key: {key}"

    def test_env_example_has_risk_keys(self):
        text = (ROOT / ".env.example").read_text(encoding="utf-8")
        for key in ("RISK_BAND_HIGH", "RISK_BAND_CRITICAL"):
            assert key in text, f"missing .env.example key: {key}"


@pytest.mark.structure
class TestPhase5Lifecycle:
    def test_migration_0003_present(self):
        assert (ROOT / "db" / "alembic" / "versions" / "0003_lifecycle_audit.py").exists()

    def test_modules_importable(self):
        import analytics.lifecycle  # noqa: F401
        import analytics.response  # noqa: F401

    def test_lifecycle_api_surface(self):
        from analytics import lifecycle

        for name in (
            "Alert",
            "escalate",
            "create_alert",
            "to_incident",
            "transition",
            "assign",
            "investigate",
            "close",
            "add_note",
            "role_can",
        ):
            assert hasattr(lifecycle, name), f"lifecycle lacks {name}"

    def test_response_api_surface(self):
        from analytics import response

        for name in ("PLAYBOOK", "ACTIONS", "recommend", "simulate", "apply", "list_actions"):
            assert hasattr(response, name), f"response lacks {name}"

    def test_dao_surface(self):
        from db import dao

        for name in ("insert_action", "list_actions", "get_alerts", "get_alert", "get_incident"):
            assert hasattr(dao, name), f"dao lacks {name}"


@pytest.mark.structure
class TestPhase6Api:
    def test_migration_0004_present(self):
        assert (ROOT / "db" / "alembic" / "versions" / "0004_api_settings.py").exists()

    def test_api_package_files_present(self):
        expected = [
            "api/main.py", "api/auth.py", "api/dependencies.py", "api/risk_view.py",
            "api/routers/auth.py", "api/routers/overview.py", "api/routers/entities.py",
            "api/routers/alerts.py", "api/routers/incidents.py", "api/routers/admin.py",
        ]
        for rel in expected:
            assert (ROOT / rel).exists(), f"missing {rel}"

    def test_app_imports_and_registers_every_endpoint(self):
        from api.main import app

        paths = set(app.openapi()["paths"])
        expected = {
            "/health", "/auth/login", "/auth/refresh", "/overview", "/users",
            "/users/{entity_id}/risk", "/entities", "/entities/{entity_id}/risk",
            "/alerts", "/alerts/{alert_id}", "/incidents", "/incidents/{incident_id}",
            "/incidents/{incident_id}/evidence", "/incidents/{incident_id}/actions",
            "/incidents/{incident_id}/notes", "/admin/users", "/admin/thresholds",
        }
        assert expected <= paths, expected - paths

    def test_auth_module_surface(self):
        from api.auth import issue_token, decode_token, issue_refresh, hash_password, verify_password

        assert callable(issue_token) and callable(decode_token)
        assert callable(issue_refresh)
        assert callable(hash_password) and callable(verify_password)

    def test_phase6_dao_surface(self):
        from db import dao

        for name in (
            "get_account_by_username", "create_account", "list_accounts",
            "get_users", "get_entities", "get_events_by_ids",
            "get_setting", "upsert_setting", "list_settings", "row_to_wire",
        ):
            assert hasattr(dao, name), f"dao lacks {name}"