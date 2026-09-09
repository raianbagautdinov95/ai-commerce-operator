"""Give channel_connections a policy too, without breaking the lookup it exists for.

Every tenant table carries `FORCE ROW LEVEL SECURITY` except this one, which was
left out on purpose: a Shopify webhook arrives knowing a shop domain and nothing
else, and the row that says which tenant owns that shop has to be readable
*before* `app.tenant_id` can be set. A policy keyed on the tenant would have made
that lookup return nothing, and with it every order notification.

The consequence was that the table mapping shops to tenants — the one that
decides whose data an order belongs to — had no database-level guarantee at all.
Application filters carried it alone, and an application filter is one forgotten
`where` away from being nothing.

So the policy permits the pre-tenant read explicitly rather than by omission:

    app.tenant_id unset  -> visible, which is the webhook's lookup
    app.tenant_id set    -> only that tenant's rows

Every authenticated request declares a tenant in `after_begin`, so from the
application's side this is a real restriction; the exemption applies only to the
handful of paths that genuinely run before anyone is identified.

One caller had to move for this. The OAuth callback asked "is this shop already
connected to another tenant?" *after* declaring its own tenant, so under this
policy it would have been answered "no" every time and let one shop be connected
twice. It asks before declaring now.

`NULLIF` because a setting that was set and then reset comes back as an empty
string rather than NULL, and an empty string is not a tenant.

Revision ID: 0021_channel_connections_rls
Revises: 0020_product_eligibility
"""
from alembic import op

revision = "0021_channel_connections_rls"
down_revision = "0020_product_eligibility"
branch_labels = None
depends_on = None

TABLE = "channel_connections"


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    if not _is_postgres():
        return  # SQLite has no RLS; development relies on the application filters.

    op.execute(f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {TABLE}")
    op.execute(f"""
        CREATE POLICY tenant_isolation ON {TABLE}
        USING (
            NULLIF(current_setting('app.tenant_id', true), '') IS NULL
            OR store_id::text = current_setting('app.tenant_id', true)
        )
        WITH CHECK (
            NULLIF(current_setting('app.tenant_id', true), '') IS NULL
            OR store_id::text = current_setting('app.tenant_id', true)
        )
    """)


def downgrade() -> None:
    if not _is_postgres():
        return
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {TABLE}")
    op.execute(f"ALTER TABLE {TABLE} NO FORCE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {TABLE} DISABLE ROW LEVEL SECURITY")
