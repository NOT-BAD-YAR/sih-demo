"""feature_windows unique (entity_ref, window_start)

Revision ID: 0002_feature_window_unique
Revises: 0001_ueba_full_schema
Create Date: 2026-08-13

Phase 4A — the engine upserts one closed window per (entity, hour) bucket;
a unique index guarantees idempotency (retries never duplicate a window).
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0002_feature_window_unique"
down_revision: Union[str, None] = "0001_ueba_full_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_feature_windows_entity_window",
        "feature_windows",
        ["entity_ref", "window_start"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_feature_windows_entity_window", "feature_windows", type_="unique")