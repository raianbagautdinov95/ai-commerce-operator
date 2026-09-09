"""Add tenant-scoped daily actual cost ledger."""
from alembic import op
import sqlalchemy as sa

revision = "0008_amazon_cost_ledger"
down_revision = "0007_amazon_sales_reports"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "amazon_cost_daily",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("store_id", sa.Uuid(), sa.ForeignKey("stores.id"), nullable=False),
        sa.Column("marketplace_id", sa.String(32), nullable=False),
        sa.Column("cost_date", sa.Date(), nullable=False),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("currency", sa.String(3)),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("source_count", sa.Integer(), nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("store_id", "marketplace_id", "cost_date", "category",
                            name="uq_amazon_cost_daily"),
    )
    for column in ("store_id", "marketplace_id", "cost_date", "category"):
        op.create_index(f"ix_amazon_cost_daily_{column}", "amazon_cost_daily", [column])


def downgrade() -> None:
    op.drop_table("amazon_cost_daily")
