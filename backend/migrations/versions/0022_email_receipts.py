"""One row per email we decided to send, so we decide once.

Stripe redelivers, the trial scheduler runs every day, and a worker can die
between reserving a send and performing it. Without a record, each of those
turns into a second copy of the same message — and the messages here are about
somebody's money, so a duplicate "your payment failed" is not a cosmetic
problem.

The unique key is (store, kind, dedupe_key). What goes in `dedupe_key` is chosen
by the caller and is the thing that makes two sends the *same* send: a Stripe
event id for a webhook, the trial's end date for a warning. The recipient is not
part of it — an address that changed does not make it a different notification —
and it is not stored in a form anyone can read back out of here.

Under row-level security like every other tenant table: which of your customers
was emailed about a failed payment is not something another customer may count.

Revision ID: 0022_email_receipts
Revises: 0021_channel_connections_rls
"""
from alembic import op
import sqlalchemy as sa

revision = "0022_email_receipts"
down_revision = "0021_channel_connections_rls"
branch_labels = None
depends_on = None

TABLE = "email_receipts"


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("store_id", sa.Uuid(), sa.ForeignKey("stores.id"), nullable=False,
                  index=True),
        sa.Column("kind", sa.String(48), nullable=False),
        sa.Column("dedupe_key", sa.String(128), nullable=False),
        # reserved -> sent | failed. `reserved` is the row that exists while the
        # send is in flight; a crash leaves one behind, and it is recoverable
        # rather than lost because the age of the row says it was abandoned.
        sa.Column("status", sa.String(16), nullable=False, server_default="reserved"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_code", sa.String(48), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("store_id", "kind", "dedupe_key",
                            name="uq_email_receipt_once"),
    )
    op.create_index("ix_email_receipts_status", TABLE, ["status"])

    if not _is_postgres():
        return  # SQLite has no RLS; development relies on the application filters.

    op.execute(f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {TABLE}")
    op.execute(f"""
        CREATE POLICY tenant_isolation ON {TABLE}
        USING (store_id::text = current_setting('app.tenant_id', true))
        WITH CHECK (store_id::text = current_setting('app.tenant_id', true))
    """)


def downgrade() -> None:
    if _is_postgres():
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {TABLE}")
        op.execute(f"ALTER TABLE {TABLE} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {TABLE} DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_email_receipts_status", table_name=TABLE)
    op.drop_table(TABLE)
