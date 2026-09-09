"""Add tenant-scoped Amazon listing, inventory, and sync state."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0006_amazon_read_sync"
down_revision = "0005_amazon_oauth"
branch_labels = None
depends_on = None
JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "amazon_listings",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("store_id", sa.Uuid(), sa.ForeignKey("stores.id"), nullable=False),
        sa.Column("marketplace_id", sa.String(32), nullable=False),
        sa.Column("sku", sa.String(128), nullable=False),
        sa.Column("asin", sa.String(16)), sa.Column("title", sa.Text()),
        sa.Column("status", JSON_TYPE, nullable=False), sa.Column("issues", JSON_TYPE, nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("store_id", "marketplace_id", "sku", name="uq_amazon_listing"),
    )
    op.create_index("ix_amazon_listings_store_id", "amazon_listings", ["store_id"])
    op.create_index("ix_amazon_listings_marketplace_id", "amazon_listings", ["marketplace_id"])
    op.create_table(
        "amazon_inventory",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("store_id", sa.Uuid(), sa.ForeignKey("stores.id"), nullable=False),
        sa.Column("marketplace_id", sa.String(32), nullable=False),
        sa.Column("seller_sku", sa.String(128), nullable=False),
        sa.Column("asin", sa.String(16)), sa.Column("fn_sku", sa.String(64)),
        sa.Column("fulfillable_quantity", sa.Integer(), nullable=False),
        sa.Column("total_quantity", sa.Integer(), nullable=False),
        sa.Column("quantities", JSON_TYPE, nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("store_id", "marketplace_id", "seller_sku", name="uq_amazon_inventory"),
    )
    op.create_index("ix_amazon_inventory_store_id", "amazon_inventory", ["store_id"])
    op.create_index("ix_amazon_inventory_marketplace_id", "amazon_inventory", ["marketplace_id"])
    op.create_table(
        "amazon_sync_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("store_id", sa.Uuid(), sa.ForeignKey("stores.id"), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("listings_count", sa.Integer(), nullable=False),
        sa.Column("inventory_count", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(64)),
    )
    op.create_index("ix_amazon_sync_runs_store_id", "amazon_sync_runs", ["store_id"])
    op.create_index("ix_amazon_sync_runs_status", "amazon_sync_runs", ["status"])


def downgrade() -> None:
    op.drop_table("amazon_sync_runs")
    op.drop_table("amazon_inventory")
    op.drop_table("amazon_listings")
