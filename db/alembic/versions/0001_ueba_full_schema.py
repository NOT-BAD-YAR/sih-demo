"""ueba full schema

Revision ID: 0001_ueba_full_schema
Revises:
Create Date: 2026-08-13

Phase 3 — every persistent table from lld.md Phase 3.
Migration order respects FK dependencies: peer_groups -> users -> entities
-> raw_events -> ... -> incidents -> analyst_actions.

Note: `event_id` is stored as a 36-char string (its natural wire representation)
rather than a native UUID so the psycopg2 DAO round-trips it as text without
extra adapters; it remains the UNIQUE dedupe key per plan.md §7.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0001_ueba_full_schema"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- peer_groups -------------------------------------------------------
    op.create_table(
        "peer_groups",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(64), unique=True, nullable=False),
        sa.Column("baseline_features", postgresql.JSONB(), nullable=True),
    )

    # --- users -------------------------------------------------------------
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("emp_id", sa.String(32), unique=True, nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("department", sa.String(64), nullable=False),
        sa.Column("peer_group_id", sa.Integer(), sa.ForeignKey("peer_groups.id"), nullable=True),
        sa.Column("role", sa.String(64), nullable=True),
        sa.Column("sensitivity_tier", sa.String(16), nullable=True),
        sa.Column("primary_device_id", sa.String(32), nullable=True),
        sa.Column("office_geo", sa.String(64), nullable=True),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )

    # --- entities ----------------------------------------------------------
    op.create_table(
        "entities",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("entity_id", sa.String(32), unique=True, nullable=False),
        sa.Column(
            "kind",
            sa.String(16),
            sa.CheckConstraint("kind IN ('device', 'server', 'app')", name="ck_entities_kind"),
            nullable=False,
        ),
        sa.Column("owner_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("location", sa.String(64), nullable=True),
        sa.Column("ip", sa.String(45), nullable=True),
    )

    # --- raw_events (dedupe key = event_id) --------------------------------
    op.create_table(
        "raw_events",
        sa.Column("event_id", sa.String(36), primary_key=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("entity_type", sa.String(16), nullable=True),
        sa.Column("entity_id", sa.String(32), nullable=True),
        sa.Column("user_id", sa.String(32), nullable=True),
        sa.Column(
            "event_type",
            sa.String(32),
            sa.CheckConstraint(
                "event_type IN ('login','logout','file_access','download','upload',"
                "'network_conn','usb','process','privilege','mfa','failure')",
                name="ck_raw_events_event_type",
            ),
            nullable=False,
        ),
        sa.Column("actor", sa.String(64), nullable=True),
        sa.Column("source_entity", sa.String(64), nullable=True),
        sa.Column("target_entity", sa.String(64), nullable=True),
        sa.Column("peer_entity", sa.String(64), nullable=True),
        sa.Column("ip", sa.String(45), nullable=True),
        sa.Column("geo", postgresql.JSONB(), nullable=True),
        sa.Column("file_path", sa.String(512), nullable=True),
        sa.Column("bytes", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "outcome",
            sa.String(16),
            sa.CheckConstraint("outcome IN ('success', 'failure')", name="ck_raw_events_outcome"),
            nullable=False,
        ),
        sa.Column(
            "sensitivity",
            sa.String(16),
            sa.CheckConstraint(
                "sensitivity IN ('public','internal','confidential','restricted')",
                name="ck_raw_events_sensitivity",
            ),
            nullable=False,
        ),
        sa.Column("raw_payload", postgresql.JSONB(), nullable=True),
    )
    op.create_index("ix_raw_events_ts", "raw_events", ["ts"])
    op.create_index("ix_raw_events_user_ts", "raw_events", ["user_id", "ts"])
    op.create_index("ix_raw_events_actor", "raw_events", ["actor"])
    op.create_index("ix_raw_events_peer_ts", "raw_events", ["peer_entity", "ts"])

    # --- behavioral_profiles ----------------------------------------------
    op.create_table(
        "behavioral_profiles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("entity_ref", sa.String(64), nullable=False),
        sa.Column(
            "level",
            sa.String(16),
            sa.CheckConstraint("level IN ('individual', 'peer_group', 'global')", name="ck_profiles_level"),
            nullable=False,
        ),
        sa.Column("feature_stats", postgresql.JSONB(), nullable=True),
        sa.Column("allowed_sets", postgresql.JSONB(), nullable=True),
        sa.Column("active_window", postgresql.JSONB(), nullable=True),
        sa.Column(
            "confidence",
            sa.String(8),
            sa.CheckConstraint("confidence IN ('HIGH', 'MED', 'LOW')", name="ck_profiles_confidence"),
            nullable=True,
        ),
        sa.Column("updated_to", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("entity_ref", "level", name="uq_profiles_entity_level"),
    )

    # --- feature_windows ---------------------------------------------------
    op.create_table(
        "feature_windows",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("entity_ref", sa.String(64), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("vector", postgresql.JSONB(), nullable=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=True),
    )

    # --- alerts ------------------------------------------------------------
    op.create_table(
        "alerts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("entity_ref", sa.String(64), nullable=True),
        sa.Column("severity", sa.String(16), nullable=True),
        sa.Column("risk", sa.Integer(), nullable=True),
        sa.Column(
            "status",
            sa.String(16),
            sa.CheckConstraint(
                "status IN ('open','assigned','investigating','resolved','false_positive')",
                name="ck_alerts_status",
            ),
            nullable=False,
            server_default=sa.text("'open'"),
        ),
        sa.Column("evidence_refs", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("assigned_to", sa.String(64), nullable=True),
    )

    # --- incidents ---------------------------------------------------------
    op.create_table(
        "incidents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("entity_ref", sa.String(64), nullable=True),
        sa.Column("severity", sa.String(16), nullable=True),
        sa.Column("risk", sa.Integer(), nullable=True),
        sa.Column(
            "status",
            sa.String(16),
            sa.CheckConstraint(
                "status IN ('open','assigned','investigating','resolved','false_positive')",
                name="ck_incidents_status",
            ),
            nullable=False,
            server_default=sa.text("'open'"),
        ),
        sa.Column("entity_chain", postgresql.JSONB(), nullable=True),
        sa.Column("related_alert_ids", postgresql.JSONB(), nullable=True),
        sa.Column("evidence_refs", postgresql.JSONB(), nullable=True),
        sa.Column("notes", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("assigned_to", sa.String(64), nullable=True),
    )

    # --- users_accounts ----------------------------------------------------
    op.create_table(
        "users_accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.String(64), unique=True, nullable=False),
        sa.Column("password_hash", sa.String(256), nullable=False),
        sa.Column(
            "role",
            sa.String(16),
            sa.CheckConstraint("role IN ('analyst', 'admin')", name="ck_accounts_role"),
            nullable=False,
        ),
        sa.Column("disabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )

    # --- analyst_actions ---------------------------------------------------
    op.create_table(
        "analyst_actions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("incident_id", sa.Integer(), sa.ForeignKey("incidents.id"), nullable=True),
        sa.Column(
            "action",
            sa.String(32),
            sa.CheckConstraint(
                "action IN ('force_mfa','revoke_session','restrict_access','isolate_device',"
                "'notify_manager','investigate')",
                name="ck_actions_action",
            ),
            nullable=False,
        ),
        sa.Column("actor_user", sa.String(64), nullable=True),
        sa.Column("impact", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )

    # --- ground_truth (evaluation only) ------------------------------------
    op.create_table(
        "ground_truth",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("scenario", sa.String(64), nullable=False),
        sa.Column("entity_id", sa.String(64), nullable=False),
        sa.Column("start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("related_event_ids", postgresql.JSONB(), nullable=True),
        sa.Column("rule", sa.String(64), nullable=True),
        sa.Column("expected_risk_band", sa.String(16), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("ground_truth")
    op.drop_table("analyst_actions")
    op.drop_table("users_accounts")
    op.drop_table("incidents")
    op.drop_table("alerts")
    op.drop_table("feature_windows")
    op.drop_table("behavioral_profiles")
    op.drop_index("ix_raw_events_peer_ts", table_name="raw_events")
    op.drop_index("ix_raw_events_actor", table_name="raw_events")
    op.drop_index("ix_raw_events_user_ts", table_name="raw_events")
    op.drop_index("ix_raw_events_ts", table_name="raw_events")
    op.drop_table("raw_events")
    op.drop_table("entities")
    op.drop_table("users")
    op.drop_table("peer_groups")