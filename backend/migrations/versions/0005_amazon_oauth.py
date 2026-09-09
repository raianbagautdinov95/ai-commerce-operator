"""Add short-lived, single-use OAuth state storage for Amazon authorization."""
from alembic import op
import sqlalchemy as sa


revision = "0005_amazon_oauth"
down_revision = "0004_encrypted_credentials"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "oauth_states",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("state_hash", sa.String(64), nullable=False),
        sa.Column("store_id", sa.Uuid(), sa.ForeignKey("stores.id"), nullable=False),
        sa.Column("actor_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("state_hash", name="uq_oauth_state_hash"),
    )
    op.create_index("ix_oauth_states_state_hash", "oauth_states", ["state_hash"], unique=True)
    op.create_index("ix_oauth_states_store_id", "oauth_states", ["store_id"])
    op.create_index("ix_oauth_states_actor_id", "oauth_states", ["actor_id"])


def downgrade() -> None:
    op.drop_table("oauth_states")
