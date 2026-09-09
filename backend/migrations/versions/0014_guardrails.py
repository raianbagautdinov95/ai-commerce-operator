"""Add per-store guardrail limits governing unattended action.

Revision ID: 0014_guardrails
Revises: 0013_operator_actions
"""
import sqlalchemy as sa
from alembic import op

revision = "0014_guardrails"
down_revision = "0013_operator_actions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "guardrail_policies",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("store_id", sa.Uuid(as_uuid=True), sa.ForeignKey("stores.id"), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("max_actions_per_day", sa.Integer(), nullable=False, server_default="20"),
        sa.Column("max_change_pct", sa.Float(), nullable=False, server_default="0.2"),
        sa.Column("auto_apply_below", sa.Float(), nullable=False, server_default="25.0"),
        sa.Column("protected_spend_per_day", sa.Float(), nullable=False, server_default="50.0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("store_id", name="uq_guardrail_policies_store"),
    )
    op.create_index("ix_guardrail_policies_store_id", "guardrail_policies", ["store_id"])


def downgrade() -> None:
    op.drop_table("guardrail_policies")
