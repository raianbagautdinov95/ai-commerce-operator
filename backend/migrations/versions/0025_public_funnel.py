"""Where the strangers come from, and how far they get.

The advertising dashboard counts a click the moment somebody taps; it cannot
see whether the page loaded, whether they pressed EVALUATE, or whether they
ever signed in. This table is the other half of that story, recorded on our
side: one row per event on the public /try page — a visit, an evaluation — with
the campaign source the link carried.

It holds no tenant's data and no person's. The visitor column is a keyed hash
of address and browser that changes every day, so two visits by the same
person on one day count once and nothing here can be turned back into an
address. No row-level policy, because there is no tenant to isolate.

Revision ID: 0025_public_funnel
Revises: 0024_product_costs
"""
from alembic import op
import sqlalchemy as sa

revision = "0025_public_funnel"
down_revision = "0024_product_costs"
branch_labels = None
depends_on = None

TABLE = "public_funnel_events"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("source", sa.String(64), nullable=True),
        sa.Column("medium", sa.String(64), nullable=True),
        sa.Column("campaign", sa.String(64), nullable=True),
        sa.Column("visitor", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_public_funnel_events_kind_created", TABLE, ["kind", "created_at"])
    op.create_index("ix_public_funnel_events_visitor", TABLE, ["visitor"])


def downgrade() -> None:
    op.drop_index("ix_public_funnel_events_visitor", table_name=TABLE)
    op.drop_index("ix_public_funnel_events_kind_created", table_name=TABLE)
    op.drop_table(TABLE)
