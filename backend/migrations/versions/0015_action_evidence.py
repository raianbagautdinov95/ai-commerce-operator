"""Label action evidence so demo and unverified impact cannot enter payroll.

Revision ID: 0015_action_evidence
Revises: 0014_guardrails
"""
import sqlalchemy as sa
from alembic import op

revision = "0015_action_evidence"
down_revision = "0014_guardrails"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing rows predate provenance tracking, so treating them as real would
    # turn an unknown number into a merchant-facing savings claim.
    op.add_column("operator_actions", sa.Column(
        "evidence_mode", sa.String(16), nullable=False, server_default="unverified"))
    op.add_column("operator_actions", sa.Column("source_type", sa.String(32), nullable=True))
    op.add_column("operator_actions", sa.Column("source_id", sa.String(255), nullable=True))
    op.create_index("ix_operator_actions_evidence_mode", "operator_actions", ["evidence_mode"])


def downgrade() -> None:
    op.drop_index("ix_operator_actions_evidence_mode", table_name="operator_actions")
    op.drop_column("operator_actions", "source_id")
    op.drop_column("operator_actions", "source_type")
    op.drop_column("operator_actions", "evidence_mode")
