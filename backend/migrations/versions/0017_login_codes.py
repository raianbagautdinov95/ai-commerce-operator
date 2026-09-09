"""Store the codes that let someone sign in with an email address.

`login_codes` is not listed among the row-level-security tables in 0016 and
must not be: it is read before the tenant is known, like `oauth_states`. The
`ALTER DEFAULT PRIVILEGES` grant made in that migration covers tables created
afterwards by the same owner, so the application role reaches this one without
another explicit GRANT.

Revision ID: 0017_login_codes
Revises: 0016_row_level_security
"""
from alembic import op
import sqlalchemy as sa

revision = "0017_login_codes"
down_revision = "0016_row_level_security"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "login_codes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_login_codes_email", "login_codes", ["email"])
    # The rate limit counts recent rows per address, so that query gets an index.
    op.create_index("ix_login_codes_created_at", "login_codes", ["created_at"])


def downgrade() -> None:
    op.drop_table("login_codes")
