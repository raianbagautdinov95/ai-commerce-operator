"""Add provider-neutral channel connections and daily commerce facts."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0010_multichannel_commerce"
down_revision = "0009_amazon_sku_cogs"
branch_labels = None
depends_on = None
JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "channel_connections",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("store_id", sa.Uuid(), sa.ForeignKey("stores.id"), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("external_account_id", sa.String(128), nullable=False),
        sa.Column("display_name", sa.String(128), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("settings", JSON_TYPE, nullable=False),
        sa.Column("connected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("store_id", "provider", "external_account_id",
                            name="uq_channel_connection"),
    )
    for column in ("store_id", "provider", "status"):
        op.create_index(f"ix_channel_connections_{column}", "channel_connections", [column])
    op.create_table(
        "commerce_daily_metrics",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("store_id", sa.Uuid(), sa.ForeignKey("stores.id"), nullable=False),
        sa.Column("channel_id", sa.Uuid(), sa.ForeignKey("channel_connections.id"), nullable=False),
        sa.Column("metric_date", sa.Date(), nullable=False),
        sa.Column("revenue", sa.Numeric(18, 2), nullable=False),
        sa.Column("refunds", sa.Numeric(18, 2), nullable=False),
        sa.Column("fees", sa.Numeric(18, 2), nullable=False),
        sa.Column("landed_cogs", sa.Numeric(18, 2), nullable=False),
        sa.Column("advertising_spend", sa.Numeric(18, 2), nullable=False),
        sa.Column("orders", sa.Integer(), nullable=False),
        sa.Column("units", sa.Integer(), nullable=False),
        sa.Column("sessions", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("costs_complete", sa.Boolean(), nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("store_id", "channel_id", "metric_date",
                            name="uq_commerce_daily_metric"),
    )
    for column in ("store_id", "channel_id", "metric_date"):
        op.create_index(f"ix_commerce_daily_metrics_{column}", "commerce_daily_metrics", [column])


def downgrade() -> None:
    op.drop_table("commerce_daily_metrics")
    op.drop_table("channel_connections")
