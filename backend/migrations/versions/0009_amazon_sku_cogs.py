"""Add report-period SKU sales and landed COGS catalog."""
from alembic import op
import sqlalchemy as sa

revision = "0009_amazon_sku_cogs"
down_revision = "0008_amazon_cost_ledger"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "amazon_sales_sku_periods",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("report_run_id", sa.Uuid(), sa.ForeignKey("amazon_report_runs.id"), nullable=False),
        sa.Column("store_id", sa.Uuid(), sa.ForeignKey("stores.id"), nullable=False),
        sa.Column("marketplace_id", sa.String(32), nullable=False),
        sa.Column("sku", sa.String(128), nullable=False),
        sa.Column("units_ordered", sa.Integer(), nullable=False),
        sa.Column("ordered_sales", sa.Numeric(18, 2), nullable=False),
        sa.Column("currency", sa.String(3)),
        sa.UniqueConstraint("report_run_id", "sku", name="uq_amazon_sales_sku_period"),
    )
    for column in ("report_run_id", "store_id", "marketplace_id"):
        op.create_index(f"ix_amazon_sales_sku_periods_{column}", "amazon_sales_sku_periods", [column])
    op.create_table(
        "amazon_sku_costs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("store_id", sa.Uuid(), sa.ForeignKey("stores.id"), nullable=False),
        sa.Column("marketplace_id", sa.String(32), nullable=False),
        sa.Column("sku", sa.String(128), nullable=False),
        sa.Column("landed_cost", sa.Numeric(18, 4), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("store_id", "marketplace_id", "sku", name="uq_amazon_sku_cost"),
    )
    op.create_index("ix_amazon_sku_costs_store_id", "amazon_sku_costs", ["store_id"])
    op.create_index("ix_amazon_sku_costs_marketplace_id", "amazon_sku_costs", ["marketplace_id"])


def downgrade() -> None:
    op.drop_table("amazon_sku_costs")
    op.drop_table("amazon_sales_sku_periods")
