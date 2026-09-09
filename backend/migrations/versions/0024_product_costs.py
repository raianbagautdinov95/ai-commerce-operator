"""What a unit cost to buy, and what each variant actually sold for.

Two tables, one purpose: make the money side of a restock provable instead of
withheld. Until now the only cost anywhere was `commerce_daily_metrics.
landed_cogs`, which is the whole shop's day. Dividing that across products would
be an invented number wearing a measured one's clothes, so profit was withheld
and every restock stayed `unverified` however carefully it was measured.

`variant_daily_metrics` is the sales half. `product_daily_metrics` answers "is
this running out", which is a product-level question; margin is not, because two
variants of one snowboard can cost different amounts to buy. Its `revenue` is
what the shop kept — after line discounts, after tax where prices include it,
after refunds — and cancelled orders never reach it.

`product_costs` is history, not a current value. A window is priced from the
days inside it, so the cost that matters is the one that was true on the day the
unit sold; overwriting last month's figure would quietly restate results that
were already priced.

Both are tenant data under FORCE ROW LEVEL SECURITY. What a competitor pays for
its stock is close to the most sensitive number in the shop.

Revision ID: 0024_product_costs
Revises: 0023_scheduler_runs
"""
from alembic import op
import sqlalchemy as sa

revision = "0024_product_costs"
down_revision = "0023_scheduler_runs"
branch_labels = None
depends_on = None

SALES = "variant_daily_metrics"
COSTS = "product_costs"


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _protect(table: str) -> None:
    """Tenant isolation enforced by the database, not by a WHERE clause.

    FORCE matters: without it the table owner sails past its own policy, and the
    migration role is the owner. The application connects as `aco_app`, which is
    NOSUPERUSER and NOBYPASSRLS precisely so this cannot be waived by accident.
    """
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
    op.execute(f"""
        CREATE POLICY tenant_isolation ON {table}
        USING (store_id::text = current_setting('app.tenant_id', true))
        WITH CHECK (store_id::text = current_setting('app.tenant_id', true))
    """)


def _unprotect(table: str) -> None:
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
    op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")


def upgrade() -> None:
    op.create_table(
        SALES,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("store_id", sa.Uuid(), sa.ForeignKey("stores.id"), nullable=False,
                  index=True),
        sa.Column("channel_id", sa.Uuid(), sa.ForeignKey("channel_connections.id"),
                  nullable=False, index=True),
        sa.Column("external_product_id", sa.String(128), nullable=False, index=True),
        sa.Column("external_variant_id", sa.String(128), nullable=False, index=True),
        sa.Column("variant_title", sa.String(255), nullable=True),
        sa.Column("metric_date", sa.Date(), nullable=False, index=True),
        sa.Column("units", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("revenue", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("refunded_units", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("refunded_revenue", sa.Numeric(18, 2), nullable=False,
                  server_default="0"),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("store_id", "channel_id", "external_variant_id",
                            "metric_date", name="uq_variant_metric_per_day"),
    )

    op.create_table(
        COSTS,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("store_id", sa.Uuid(), sa.ForeignKey("stores.id"), nullable=False,
                  index=True),
        sa.Column("channel_id", sa.Uuid(), sa.ForeignKey("channel_connections.id"),
                  nullable=True, index=True),
        sa.Column("external_product_id", sa.String(128), nullable=False, index=True),
        # "" means every variant of this product. Not NULL, because NULLs are
        # distinct from each other in a unique index and the fallback would then
        # be insertable twice for the same day.
        sa.Column("external_variant_id", sa.String(128), nullable=False,
                  server_default="", index=True),
        sa.Column("variant_title", sa.String(255), nullable=True),
        # Four places. A landed cost of 12.3456 a unit is ordinary, and rounding
        # at rest would make the margin depend on when it was stored.
        sa.Column("amount", sa.Numeric(18, 4), nullable=False),
        # The landed cost split the way a seller thinks about it. `amount` is
        # what the measurement uses and is always the total; these two are kept
        # so a figure can be corrected without retyping it, and are null for a
        # value read out of Shopify, which reports one number.
        sa.Column("purchase_amount", sa.Numeric(18, 4), nullable=True),
        sa.Column("extra_amount", sa.Numeric(18, 4), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False, index=True),
        sa.Column("source", sa.String(16), nullable=False),        # shopify | manual
        sa.Column("verification", sa.String(16), nullable=False),  # confirmed | reported
        sa.Column("entered_by", sa.String(64), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("store_id", "external_product_id", "external_variant_id",
                            "effective_from", "source", name="uq_product_cost_per_day"),
    )
    # The lookup the measurement performs on every priced unit: this variant,
    # the newest value that was already in force on the day it sold.
    op.create_index("ix_product_costs_lookup", COSTS,
                    ["store_id", "external_variant_id", "effective_from"])

    if not _is_postgres():
        return  # SQLite has no RLS; development relies on the application filters.
    _protect(SALES)
    _protect(COSTS)


def downgrade() -> None:
    if _is_postgres():
        _unprotect(COSTS)
        _unprotect(SALES)
    op.drop_index("ix_product_costs_lookup", table_name=COSTS)
    op.drop_table(COSTS)
    op.drop_table(SALES)
