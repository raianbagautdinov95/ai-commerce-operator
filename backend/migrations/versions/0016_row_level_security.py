"""Enforce tenant isolation in PostgreSQL, not only in application filters.

Until now one forgotten WHERE clause was the whole distance between a query and
another tenant's data. Application filters are the right first line — they are
readable and testable — but they defend against nothing that a mistake in this
codebase can cause, which is the likeliest cause there is.

Row-level security moves the guarantee under the application. A query missing
its tenant filter now returns nothing instead of somebody else's rows.

Two things this depends on, both of which are easy to get wrong:

1. **The application must not connect as a superuser.** RLS never applies to
   superusers or roles with BYPASSRLS, so enabling it while the API connects as
   the database owner produces a policy that is decorative. This migration
   creates `aco_app`, a plain LOGIN role, and the API and worker must use it.
   Migrations keep running as the owner, which is why they still work.

2. **Two tables are deliberately left out.** `oauth_states` and
   `channel_connections` are read *before* the tenant is known — an OAuth
   callback arrives with a state hash and a webhook with a shop domain, and both
   have to find out which tenant they belong to. A policy keyed on the current
   tenant cannot express "look this up to discover the tenant". They stay under
   application filters, and both are looked up by a value the caller had to
   prove it possessed.

Revision ID: 0016_row_level_security
Revises: 0015_action_evidence
"""
import os

from alembic import op

revision = "0016_row_level_security"
down_revision = "0015_action_evidence"
branch_labels = None
depends_on = None

APP_ROLE = os.getenv("APP_DB_ROLE", "aco_app")

# Every table holding one tenant's business data.
PROTECTED_TABLES = (
    "amazon_cost_daily", "amazon_inventory", "amazon_listings", "amazon_report_runs",
    "amazon_sales_daily", "amazon_sales_sku_periods", "amazon_sku_costs",
    "amazon_sync_runs", "audit_events", "commerce_daily_metrics", "guardrail_policies",
    "idempotency_records", "integration_credentials", "operator_actions",
    "product_evaluations", "products", "recommendations", "subscriptions",
    "webhook_deliveries",
)

# Read before the tenant is known; see the module docstring.
UNPROTECTED_BY_DESIGN = ("oauth_states", "channel_connections")


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    if not _is_postgres():
        return  # SQLite has no RLS; development relies on the application filters.

    password = os.getenv("APP_DB_PASSWORD")
    if password:
        op.execute(f"""
        DO $$
        BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
            CREATE ROLE {APP_ROLE} LOGIN PASSWORD '{password}';
          ELSE
            ALTER ROLE {APP_ROLE} WITH LOGIN PASSWORD '{password}';
          END IF;
        END $$;
        """)
        # Explicitly not a superuser and explicitly not exempt: either would make
        # every policy below silently inert.
        op.execute(f"ALTER ROLE {APP_ROLE} NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE")
        op.execute(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}")
        op.execute(
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {APP_ROLE}")
        op.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE}")
        op.execute("ALTER DEFAULT PRIVILEGES IN SCHEMA public "
                   f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {APP_ROLE}")
        op.execute("ALTER DEFAULT PRIVILEGES IN SCHEMA public "
                   f"GRANT USAGE, SELECT ON SEQUENCES TO {APP_ROLE}")

    for table in PROTECTED_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        # FORCE so the guarantee holds even when the owner is the one querying.
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        # current_setting(..., true) yields NULL when unset, and NULL = anything
        # is never true, so a session that forgot to declare its tenant sees
        # nothing rather than everything. Failing closed is the point.
        op.execute(f"""
            CREATE POLICY tenant_isolation ON {table}
            USING (store_id::text = current_setting('app.tenant_id', true))
            WITH CHECK (store_id::text = current_setting('app.tenant_id', true))
        """)


def downgrade() -> None:
    if not _is_postgres():
        return
    for table in PROTECTED_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
