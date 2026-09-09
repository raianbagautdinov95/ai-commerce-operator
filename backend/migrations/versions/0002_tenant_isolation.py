"""Attach every product evaluation to its tenant store."""
from alembic import op
import sqlalchemy as sa


revision = "0002_tenant_isolation"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("product_evaluations") as batch:
        batch.add_column(
            sa.Column(
                "store_id",
                sa.Uuid(),
                sa.ForeignKey("stores.id", name="fk_product_evaluations_store_id"),
                nullable=True,
            )
        )
    op.execute(
        "UPDATE product_evaluations SET store_id = ("
        "SELECT stores.id FROM stores "
        "WHERE stores.user_id = product_evaluations.user_id "
        "ORDER BY stores.created_at ASC LIMIT 1) "
        "WHERE store_id IS NULL"
    )
    connection = op.get_bind()
    missing = connection.execute(
        sa.text("SELECT COUNT(*) FROM product_evaluations WHERE store_id IS NULL")
    ).scalar_one()
    if missing:
        raise RuntimeError("Cannot migrate evaluations without a matching store.")
    with op.batch_alter_table("product_evaluations") as batch:
        batch.alter_column("store_id", existing_type=sa.Uuid(), nullable=False)
        batch.create_index("ix_product_evaluations_store_id", ["store_id"])


def downgrade() -> None:
    with op.batch_alter_table("product_evaluations") as batch:
        batch.drop_index("ix_product_evaluations_store_id")
        batch.drop_column("store_id")
