"""Replace plaintext store tokens with versioned encrypted credentials."""
from alembic import op
import sqlalchemy as sa
import datetime as dt
import uuid

from app.credential_crypto import encrypt_credential, validate_credential_encryption_config


revision = "0004_encrypted_credentials"
down_revision = "0003_audit_idempotency"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    plaintext_rows = connection.execute(
        sa.text("SELECT id, sp_api_refresh_token FROM stores WHERE sp_api_refresh_token IS NOT NULL")
    ).all()
    if plaintext_rows:
        validate_credential_encryption_config(required=True)
    op.create_table(
        "integration_credentials",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("store_id", sa.Uuid(), sa.ForeignKey("stores.id"), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("encrypted_secret", sa.Text(), nullable=False),
        sa.Column("nonce", sa.String(64), nullable=False),
        sa.Column("key_version", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("store_id", "provider", name="uq_integration_credential_provider"),
    )
    op.create_index("ix_integration_credentials_store_id", "integration_credentials", ["store_id"])
    now = dt.datetime.now(dt.timezone.utc)
    for store_id, plaintext in plaintext_rows:
        encrypted = encrypt_credential(
            plaintext, store_id=str(store_id), provider="amazon-sp-api"
        )
        connection.execute(
            sa.text(
                "INSERT INTO integration_credentials "
                "(id, store_id, provider, encrypted_secret, nonce, key_version, created_at, updated_at) "
                "VALUES (:id, :store_id, :provider, :secret, :nonce, :version, :created_at, :updated_at)"
            ),
            {
                "id": str(uuid.uuid4()), "store_id": store_id, "provider": "amazon-sp-api",
                "secret": encrypted.ciphertext, "nonce": encrypted.nonce,
                "version": encrypted.key_version, "created_at": now, "updated_at": now,
            },
        )
    with op.batch_alter_table("stores") as batch:
        batch.drop_column("sp_api_refresh_token")


def downgrade() -> None:
    with op.batch_alter_table("stores") as batch:
        batch.add_column(sa.Column("sp_api_refresh_token", sa.Text(), nullable=True))
    op.drop_table("integration_credentials")
