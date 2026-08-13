"""Phase 3 — db package unit tests (no Postgres needed)."""

import pytest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent

pytestmark = pytest.mark.unit


class TestStructure:
    def test_db_modules_importable(self):
        import db  # noqa: F401
        import db.conn  # noqa: F401
        import db.dao  # noqa: F401
        import db.seed  # noqa: F401
        import db.persist  # noqa: F401
        import db.passwords  # noqa: F401

    def test_phase3_files_present(self):
        for rel in (
            "db/conn.py",
            "db/dao.py",
            "db/seed.py",
            "db/persist.py",
            "db/passwords.py",
            "db/__init__.py",
        ):
            assert (ROOT / rel).exists(), f"missing {rel}"

    def test_phase3_migration_present(self):
        versions = ROOT / "db" / "alembic" / "versions"
        assert any(f.name.startswith("0001_") and f.name.endswith(".py") for f in versions.iterdir()), (
            "no 0001_... migration under db/alembic/versions"
        )

    def test_dao_exposes_core_api(self):
        import db.dao as dao

        for fn in (
            "insert_event",
            "get_event",
            "count_events",
            "list_recent_events",
            "events_for_user",
            "schema_tables",
        ):
            assert hasattr(dao, fn), f"dao lacks {fn}"

    def test_schema_table_list_covers_lld(self):
        from db.dao import SCHEMA_TABLES

        expected = {
            "users", "peer_groups", "entities", "raw_events", "behavioral_profiles",
            "feature_windows", "alerts", "incidents", "users_accounts",
            "analyst_actions", "ground_truth",
        }
        assert set(SCHEMA_TABLES) == expected


class TestConn:
    def test_normalize_dsn_strips_sqlalchemy_prefix(self):
        from db.conn import normalize_dsn

        assert normalize_dsn("postgresql+psycopg2://u:p@h/db") == "postgresql://u:p@h/db"
        assert normalize_dsn("postgresql://u:p@h/db") == "postgresql://u:p@h/db"


class TestPasswords:
    def test_hash_verify_roundtrip(self):
        from db.passwords import hash_password, verify_password

        stored = hash_password("s3cret!")
        assert stored.startswith("pbkdf2_sha256$")
        assert verify_password("s3cret!", stored) is True

    def test_wrong_password_rejected(self):
        from db.passwords import hash_password, verify_password

        stored = hash_password("right")
        assert verify_password("wrong", stored) is False

    def test_malformed_stored_rejected(self):
        from db.passwords import verify_password

        assert verify_password("x", "not-a-valid-hash") is False
        assert verify_password("x", "plain********") is False

    def test_unique_salts(self):
        from db.passwords import hash_password

        assert hash_password("same") != hash_password("same")


class TestWireMapping:
    def test_wire_to_row_maps_canonical_fields(self):
        from db.dao import _wire_to_row
        from datetime import datetime, timezone

        payload = {
            "event_id": "11111111-2222-3333-4444-555555555555",
            "ts": "2026-01-01T09:00:00+00:00",
            "ingested_at": "2026-01-01T09:00:01+00:00",
            "entity_type": "user",
            "entity_id": "EMP001",
            "user_id": "EMP001",
            "event_type": "login",
            "actor": "EMP001",
            "source_entity": "LPT-001",
            "target_entity": "LPT-001",
            "peer_entity": "",
            "ip": "10.0.0.1",
            "geo": {"city": "Chennai", "lat": 13.08, "lon": 80.27},
            "file_path": None,
            "bytes": 0,
            "outcome": "success",
            "sensitivity": "internal",
            "raw_payload": {"k": "v"},
        }
        row = _wire_to_row(payload)
        assert row["event_id"] == payload["event_id"]
        assert row["event_type"] == "login"
        assert isinstance(row["ts"], datetime)
        assert '"city"' in row["geo"]
        assert '"k"' in row["raw_payload"]


class TestSeedConstants:
    def test_peer_group_count(self):
        from db.seed import PEER_GROUP_NAMES

        assert len(PEER_GROUP_NAMES) == 7
        assert set(PEER_GROUP_NAMES) >= {"HR", "Finance", "Security", "Administrators", "Contractors"}

    def test_demo_accounts(self):
        from db.seed import DEMO_ACCOUNTS

        assert DEMO_ACCOUNTS == {"analyst": "analyst", "admin": "admin"}