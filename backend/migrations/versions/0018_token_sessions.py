"""Make an issued token revocable.

`token_sessions` holds one row per token handed out. The middleware reads it on
every authenticated request, before the tenant is known, so — like
`login_codes` and `oauth_states` — it stays out of the row-level-security
policies in 0016. The `ALTER DEFAULT PRIVILEGES` grant made there covers tables
created afterwards by the same owner, so the application role reaches this one
without another explicit GRANT.

Tokens minted before this migration carry no `jti` and are refused from here
on. That is deliberate: an unrevocable token is exactly what this table exists
to abolish, and keeping a grandfathered set of them would leave the hole open
for a week while pretending it was closed. Everyone signs in again once.

Revision ID: 0018_token_sessions
Revises: 0017_login_codes
"""
from alembic import op
import sqlalchemy as sa

revision = "0018_token_sessions"
down_revision = "0017_login_codes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "token_sessions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("store_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("method", sa.String(32), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
    )
    # Listing a person's sessions, and revoking all of them at once, both filter
    # on the user; expiry is what a cleanup job would sweep on.
    op.create_index("ix_token_sessions_user_id", "token_sessions", ["user_id"])
    op.create_index("ix_token_sessions_store_id", "token_sessions", ["store_id"])
    op.create_index("ix_token_sessions_expires_at", "token_sessions", ["expires_at"])


def downgrade() -> None:
    op.drop_table("token_sessions")
