"""Initial application schema before tenant-owned evaluations."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None

JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("email"),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    op.create_table(
        "stores",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("marketplace", sa.String(16), nullable=False),
        sa.Column("seller_id", sa.String(128)),
        sa.Column("sp_api_refresh_token", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_stores_user_id", "stores", ["user_id"])
    op.create_table(
        "product_evaluations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("inputs", JSON_TYPE, nullable=False),
        sa.Column("economics", JSON_TYPE, nullable=False),
        sa.Column("subscores", JSON_TYPE, nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("verdict", sa.String(16), nullable=False),
        sa.Column("explanation", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_product_evaluations_user_id", "product_evaluations", ["user_id"])
    op.create_table(
        "products",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("store_id", sa.Uuid(), sa.ForeignKey("stores.id"), nullable=False),
        sa.Column("asin", sa.String(16), nullable=False),
        sa.Column("title", sa.Text()),
        sa.Column("price", sa.Float()),
        sa.Column("cogs", sa.Float()),
    )
    op.create_index("ix_products_store_id", "products", ["store_id"])
    op.create_index("ix_products_asin", "products", ["asin"])
    op.create_table(
        "recommendations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("store_id", sa.Uuid(), sa.ForeignKey("stores.id"), nullable=False),
        sa.Column("module", sa.String(32), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("detail", JSON_TYPE, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_recommendations_store_id", "recommendations", ["store_id"])


def downgrade() -> None:
    op.drop_table("recommendations")
    op.drop_table("products")
    op.drop_table("product_evaluations")
    op.drop_table("stores")
    op.drop_table("users")
