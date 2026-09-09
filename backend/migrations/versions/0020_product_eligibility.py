"""Enough about a product to know a restock proposal would be nonsense.

The first store this ran against sells a gift card. It tracks inventory, it had
been bought twice, and Shopify reported minus two in stock — so every test the
engine applied said "this is running out, restock it". Nothing was wrong with
the arithmetic and the proposal was still absurd: nobody restocks a gift card,
and a seller shown that advice learns the Operator does not understand their
shop.

Two flags tell a physical product from a digital one, and the same catalogue
demonstrates both: `is_gift_card` is true for exactly the product that should
never be proposed, and `requires_shipping` is false for it and true for every
snowboard. Both are nullable because a product synced before this migration has
neither, and unknown must not read as eligible.

Revision ID: 0020_product_eligibility
Revises: 0019_channel_products
"""
from alembic import op
import sqlalchemy as sa

revision = "0020_product_eligibility"
down_revision = "0019_channel_products"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("channel_products",
                  sa.Column("is_gift_card", sa.Boolean(), nullable=True))
    op.add_column("channel_products",
                  sa.Column("requires_shipping", sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column("channel_products", "requires_shipping")
    op.drop_column("channel_products", "is_gift_card")
