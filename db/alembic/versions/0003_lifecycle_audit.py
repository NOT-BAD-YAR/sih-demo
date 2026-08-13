"""analyst_actions audit fields + updated_by on alerts/incidents

Revision ID: 0003_lifecycle_audit
Revises: 0002_feature_window_unique
Create Date: 2026-08-13

Phase 5 — Alert/Incident/Response engine.
  * alerts / incidents gain `updated_by` (the LLD requires every lifecycle
    transition to write `updated_at` + `updated_by`).
  * analyst_actions gains `status` (audited action outcome, e.g.
    `applied(simulated)`) and `simulated_state` (JSONB side-effect the
    dashboard shows as the consequence of a simulated response action).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0003_lifecycle_audit"
down_revision: Union[str, None] = "0002_feature_window_unique"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("alerts", sa.Column("updated_by", sa.String(64), nullable=True))
    op.add_column("incidents", sa.Column("updated_by", sa.String(64), nullable=True))

    op.add_column(
        "analyst_actions",
        sa.Column(
            "status",
            sa.String(24),
            nullable=False,
            server_default=sa.text("'applied(simulated)'"),
        ),
    )
    op.add_column("analyst_actions", sa.Column("simulated_state", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("analyst_actions", "simulated_state")
    op.drop_column("analyst_actions", "status")
    op.drop_column("incidents", "updated_by")
    op.drop_column("alerts", "updated_by")