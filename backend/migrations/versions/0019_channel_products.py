"""Per-product sales and stock, so a store-wide total can stop being the answer.

`commerce_daily_metrics` knows what the shop did on a day. It cannot say which
product did it, so nothing could ever propose acting on one — and a restock
judged against shop-wide revenue would be judged against every other product's
noise as well.

Two tables. `channel_products` is the current shelf: title, status, and units on
hand. `on_hand` is nullable on purpose — None means the channel does not count
this product's stock, which is not the same as none left, and reading one as the
other would report every untracked product as about to run out.
`product_daily_metrics` is the history: units and money per product per day,
which is what a before-and-after comparison needs.

Both carry `store_id` and both join the row-level-security policies from 0016.
A tenant table without one is a tenant table in name only: `FORCE ROW LEVEL
SECURITY` so the guarantee holds even when the owner is querying, and a policy
that fails closed, because `current_setting('app.tenant_id', true)` is NULL when
nobody declared a tenant and NULL = anything is never true.

Revision ID: 0019_channel_products
Revises: 0018_token_sessions
"""
from alembic import op
import sqlalchemy as sa

revision = "0019_channel_products"
down_revision = "0018_token_sessions"
branch_labels = None
depends_on = None

PROTECTED_TABLES = ("channel_products", "product_daily_metrics")


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    op.create_table(
        "channel_products",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("store_id", sa.Uuid(), sa.ForeignKey("stores.id"), nullable=False, index=True),
        sa.Column("channel_id", sa.Uuid(), sa.ForeignKey("channel_connections.id"),
                  nullable=False, index=True),
        sa.Column("external_product_id", sa.String(length=128), nullable=False, index=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=True),
        # Nullable: unknown stock is not empty stock.
        sa.Column("on_hand", sa.Integer(), nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.UniqueConstraint("store_id", "channel_id", "external_product_id",
                            name="uq_channel_products_identity"),
    )
    op.create_table(
        "product_daily_metrics",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("store_id", sa.Uuid(), sa.ForeignKey("stores.id"), nullable=False, index=True),
        sa.Column("channel_id", sa.Uuid(), sa.ForeignKey("channel_connections.id"),
                  nullable=False, index=True),
        sa.Column("external_product_id", sa.String(length=128), nullable=False, index=True),
        sa.Column("metric_date", sa.Date(), nullable=False, index=True),
        sa.Column("units", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("revenue", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.UniqueConstraint("store_id", "channel_id", "external_product_id", "metric_date",
                            name="uq_product_daily_identity"),
    )

    if not _is_postgres():
        return  # SQLite has no RLS; development relies on the application filters.

    for table in PROTECTED_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f"""
            CREATE POLICY tenant_isolation ON {table}
            USING (store_id::text = current_setting('app.tenant_id', true))
            WITH CHECK (store_id::text = current_setting('app.tenant_id', true))
        """)


def downgrade() -> None:
    if _is_postgres():
        for table in PROTECTED_TABLES:
            op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
            op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
            op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    op.drop_table("product_daily_metrics")
    op.drop_table("channel_products")
