"""Add the operator action ledger that prices applied recommendations.

Revision ID: 0013_operator_actions
Revises: 0012_subscriptions
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0013_operator_actions"
down_revision = "0012_subscriptions"
branch_labels = None
depends_on = None

_JSON = sa.JSON().with_variant(postgresql.JSONB, "postgresql")


def upgrade() -> None:
    op.create_table(
        "operator_actions",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("store_id", sa.Uuid(as_uuid=True), sa.ForeignKey("stores.id"), nullable=False),
        sa.Column("recommendation_id", sa.Uuid(as_uuid=True),
                  sa.ForeignKey("recommendations.id"), nullable=True),
        sa.Column("module", sa.String(32), nullable=False),
        sa.Column("action_type", sa.String(48), nullable=False),
        sa.Column("target", sa.String(255), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="proposed"),
        sa.Column("applied_by", sa.String(16), nullable=True),
        sa.Column("projected_impact", sa.Numeric(18, 2), nullable=True),
        sa.Column("baseline", _JSON, nullable=True),
        sa.Column("outcome", _JSON, nullable=True),
        sa.Column("impact", _JSON, nullable=True),
        sa.Column("revert_to", _JSON, nullable=True),
        sa.Column("measurement_days", sa.Integer(), nullable=False, server_default="14"),
        sa.Column("currency", sa.String(3), nullable=False, server_default="USD"),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("proposed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("measured_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_operator_actions_store_id", "operator_actions", ["store_id"])
    op.create_index("ix_operator_actions_status", "operator_actions", ["status"])
    op.create_index("ix_operator_actions_module", "operator_actions", ["module"])
    op.create_index("ix_operator_actions_recommendation_id", "operator_actions",
                    ["recommendation_id"])


def downgrade() -> None:
    op.drop_table("operator_actions")
