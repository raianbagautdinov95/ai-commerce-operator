import base64
import json

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app import credentials
from app.credential_crypto import (
    CredentialDecryptionError,
    EncryptedCredential,
    decrypt_credential,
    encrypt_credential,
    validate_credential_encryption_config,
)
from app.db import crud, models
from app.db.models import Base


def _key(value: bytes) -> str:
    return base64.b64encode(value * 32).decode()


def _configure(monkeypatch, active="v1"):
    monkeypatch.setenv(
        "CREDENTIAL_ENCRYPTION_KEYS",
        json.dumps({"v1": _key(b"a"), "v2": _key(b"b")}),
    )
    monkeypatch.setenv("CREDENTIAL_ACTIVE_KEY_VERSION", active)


def _fresh_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def test_authenticated_encryption_rejects_wrong_tenant(monkeypatch):
    _configure(monkeypatch)
    encrypted = encrypt_credential("refresh-token", store_id="store-a", provider="amazon-sp-api")
    assert decrypt_credential(encrypted, store_id="store-a", provider="amazon-sp-api") == "refresh-token"
    with pytest.raises(CredentialDecryptionError):
        decrypt_credential(encrypted, store_id="store-b", provider="amazon-sp-api")


def test_ciphertext_tampering_is_rejected(monkeypatch):
    _configure(monkeypatch)
    encrypted = encrypt_credential("refresh-token", store_id="store-a", provider="amazon-sp-api")
    damaged = EncryptedCredential(encrypted.ciphertext[:-2] + "AA", encrypted.nonce, encrypted.key_version)
    with pytest.raises(CredentialDecryptionError):
        decrypt_credential(damaged, store_id="store-a", provider="amazon-sp-api")


def test_store_load_and_key_rotation(monkeypatch):
    _configure(monkeypatch, active="v1")
    db = _fresh_session()
    store = crud.get_or_create_dev_store(db)
    metadata = credentials.store_credential(
        db, store_id=store.id, provider="amazon-sp-api", secret="refresh-token"
    )
    assert "refresh-token" not in repr(metadata)
    assert credentials.load_credential(db, store_id=store.id, provider="amazon-sp-api") == "refresh-token"

    _configure(monkeypatch, active="v2")
    rotated = credentials.rotate_credential(db, store_id=store.id, provider="amazon-sp-api")
    assert rotated["key_version"] == "v2"
    row = db.scalar(select(models.IntegrationCredential))
    assert row.key_version == "v2"
    assert "refresh-token" not in row.encrypted_secret


def test_credentials_declare_their_tenant_for_each_transaction(monkeypatch):
    _configure(monkeypatch)
    db = _fresh_session()
    store = crud.get_or_create_dev_store(db)
    declared = []
    monkeypatch.setattr(credentials, "declare_tenant", lambda _db, tenant: declared.append(tenant))

    credentials.store_credential(
        db, store_id=store.id, provider="shopify:test", secret="access-token"
    )
    assert credentials.load_credential(
        db, store_id=store.id, provider="shopify:test"
    ) == "access-token"
    assert declared == [store.id, store.id]


def test_invalid_key_length_is_rejected(monkeypatch):
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEYS", json.dumps({"v1": base64.b64encode(b"short").decode()}))
    monkeypatch.setenv("CREDENTIAL_ACTIVE_KEY_VERSION", "v1")
    with pytest.raises(RuntimeError, match="32 bytes"):
        validate_credential_encryption_config(required=True)
