"""Add OAuth context and durable webhook replay protection."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0011_shopify_oauth_webhooks"
down_revision = "0010_multichannel_commerce"
branch_labels = None
depends_on = None
JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.add_column("oauth_states", sa.Column("context", JSON_TYPE, nullable=True))
    op.create_table(
        "webhook_deliveries",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("store_id", sa.Uuid(), sa.ForeignKey("stores.id"), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("delivery_id", sa.String(128), nullable=False),
        sa.Column("topic", sa.String(64), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(64)),
        sa.UniqueConstraint("provider", "delivery_id", name="uq_webhook_delivery"),
    )
    for column in ("store_id", "provider", "topic", "status"):
        op.create_index(f"ix_webhook_deliveries_{column}", "webhook_deliveries", [column])


def downgrade() -> None:
    op.drop_table("webhook_deliveries")
    op.drop_column("oauth_states", "context")
