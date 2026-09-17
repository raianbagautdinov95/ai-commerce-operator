"""Tenant-scoped persistence facade for encrypted integration credentials."""
from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from .credential_crypto import EncryptedCredential, decrypt_credential, encrypt_credential
from .db import models
from .db.session import declare_tenant
from .security import current_principal


def _assert_tenant(store_id: uuid.UUID) -> None:
    principal = current_principal()
    if principal and uuid.UUID(principal.tenant_id) != store_id:
        raise PermissionError("Cannot access credentials for another tenant.")


def store_credential(db: Session, *, store_id: uuid.UUID, provider: str, secret: str) -> dict:
    _assert_tenant(store_id)
    # OAuth callbacks and webhooks discover their tenant from signed external
    # data, after the request session has already begun without a principal.
    # Declare it here as well as in callers so every credential transaction is
    # protected by (and permitted through) PostgreSQL RLS.
    declare_tenant(db, store_id)
    provider = provider.lower().strip()
    encrypted = encrypt_credential(secret, store_id=str(store_id), provider=provider)
    row = db.scalar(select(models.IntegrationCredential).where(
        models.IntegrationCredential.store_id == store_id,
        models.IntegrationCredential.provider == provider,
    ))
    if row is None:
        row = models.IntegrationCredential(store_id=store_id, provider=provider)
    row.encrypted_secret = encrypted.ciphertext
    row.nonce = encrypted.nonce
    row.key_version = encrypted.key_version
    row.updated_at = dt.datetime.now(dt.timezone.utc)
    db.add(row)
    db.flush()
    result = {"id": str(row.id), "provider": row.provider, "key_version": row.key_version,
              "updated_at": row.updated_at}
    db.commit()
    return result


def delete_credential(db: Session, *, store_id: uuid.UUID, provider: str,
                      commit: bool = True) -> bool:
    """Remove a tenant's credential without touching any imported facts.

    The caller may keep this inside a larger transaction with the channel
    status and its audit event.  This is used when a merchant deliberately
    switches stores: the Operator must lose access to the old one, while the
    read-only aggregate history remains separately scoped to that channel.
    """
    _assert_tenant(store_id)
    declare_tenant(db, store_id)
    provider = provider.lower().strip()
    row = db.scalar(select(models.IntegrationCredential).where(
        models.IntegrationCredential.store_id == store_id,
        models.IntegrationCredential.provider == provider,
    ))
    if row is None:
        return False
    db.delete(row)
    db.flush()
    if commit:
        db.commit()
    return True


def load_credential(db: Session, *, store_id: uuid.UUID, provider: str) -> str | None:
    _assert_tenant(store_id)
    declare_tenant(db, store_id)
    provider = provider.lower().strip()
    row = db.scalar(select(models.IntegrationCredential).where(
        models.IntegrationCredential.store_id == store_id,
        models.IntegrationCredential.provider == provider,
    ))
    if row is None:
        return None
    return decrypt_credential(
        EncryptedCredential(row.encrypted_secret, row.nonce, row.key_version),
        store_id=str(store_id), provider=provider,
    )


def rotate_credential(db: Session, *, store_id: uuid.UUID, provider: str) -> dict | None:
    secret = load_credential(db, store_id=store_id, provider=provider)
    return None if secret is None else store_credential(
        db, store_id=store_id, provider=provider, secret=secret
    )
