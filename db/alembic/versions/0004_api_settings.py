"""api_settings — tunable engine thresholds for the Phase 6 admin API

Revision ID: 0004_api_settings
Revises: 0003_lifecycle_audit
Create Date: 2026-08-13

Phase 6 — the admin API stores engine thresholds in a `settings` table so
they are tunable without a redeploy (LLD: "Thresholds stored in `settings`
table, read by engine risk at invocation"). Values are JSONB so a key can
carry any primitive or small structure.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0004_api_settings"
down_revision: Union[str, None] = "0003_lifecycle_audit"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "settings",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("value", postgresql.JSONB(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("settings")