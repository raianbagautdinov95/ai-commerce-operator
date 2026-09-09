"""Add tenant-scoped Amazon sales and traffic reports."""
from alembic import op
import sqlalchemy as sa


revision = "0007_amazon_sales_reports"
down_revision = "0006_amazon_read_sync"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "amazon_report_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("store_id", sa.Uuid(), sa.ForeignKey("stores.id"), nullable=False),
        sa.Column("marketplace_id", sa.String(32), nullable=False),
        sa.Column("report_type", sa.String(64), nullable=False),
        sa.Column("amazon_report_id", sa.String(64)),
        sa.Column("report_document_id", sa.String(128)),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("date_start", sa.Date(), nullable=False),
        sa.Column("date_end", sa.Date(), nullable=False),
        sa.Column("rows_count", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(64)),
    )
    op.create_index("ix_amazon_report_runs_store_id", "amazon_report_runs", ["store_id"])
    op.create_index("ix_amazon_report_runs_marketplace_id", "amazon_report_runs", ["marketplace_id"])
    op.create_index("ix_amazon_report_runs_status", "amazon_report_runs", ["status"])
    op.create_table(
        "amazon_sales_daily",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("store_id", sa.Uuid(), sa.ForeignKey("stores.id"), nullable=False),
        sa.Column("marketplace_id", sa.String(32), nullable=False),
        sa.Column("sales_date", sa.Date(), nullable=False),
        sa.Column("ordered_sales", sa.Numeric(18, 2), nullable=False),
        sa.Column("currency", sa.String(3)),
        sa.Column("units_ordered", sa.Integer(), nullable=False),
        sa.Column("order_items", sa.Integer(), nullable=False),
        sa.Column("page_views", sa.Integer(), nullable=False),
        sa.Column("sessions", sa.Integer(), nullable=False),
        sa.Column("buy_box_percentage", sa.Float()),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("store_id", "marketplace_id", "sales_date",
                            name="uq_amazon_sales_daily"),
    )
    op.create_index("ix_amazon_sales_daily_store_id", "amazon_sales_daily", ["store_id"])
    op.create_index("ix_amazon_sales_daily_marketplace_id", "amazon_sales_daily", ["marketplace_id"])
    op.create_index("ix_amazon_sales_daily_sales_date", "amazon_sales_daily", ["sales_date"])


def downgrade() -> None:
    op.drop_table("amazon_sales_daily")
    op.drop_table("amazon_report_runs")
