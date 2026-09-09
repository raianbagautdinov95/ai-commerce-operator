"""Authenticated encryption for tenant integration credentials."""
from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class CredentialConfigurationError(RuntimeError):
    pass


class CredentialDecryptionError(RuntimeError):
    pass


@dataclass(frozen=True)
class EncryptedCredential:
    ciphertext: str
    nonce: str
    key_version: str


def _keyring() -> dict[str, bytes]:
    raw = os.getenv("CREDENTIAL_ENCRYPTION_KEYS", "")
    if not raw:
        return {}
    try:
        configured = json.loads(raw)
        if not isinstance(configured, dict):
            raise ValueError
        keys = {str(version): base64.b64decode(value, validate=True)
                for version, value in configured.items()}
    except Exception as exc:
        raise CredentialConfigurationError(
            "CREDENTIAL_ENCRYPTION_KEYS must be a JSON object of base64 keys."
        ) from exc
    if any(len(key) != 32 for key in keys.values()):
        raise CredentialConfigurationError("Every credential encryption key must be 32 bytes.")
    return keys


def validate_credential_encryption_config(*, required: bool = False) -> None:
    keys = _keyring()
    active = os.getenv("CREDENTIAL_ACTIVE_KEY_VERSION", "")
    if required and not keys:
        raise CredentialConfigurationError("Credential encryption keys are required in production.")
    if keys and active not in keys:
        raise CredentialConfigurationError("CREDENTIAL_ACTIVE_KEY_VERSION is missing from the keyring.")


def _aad(store_id: str, provider: str) -> bytes:
    return f"aco-credential:{store_id}:{provider.lower()}".encode()


def encrypt_credential(plaintext: str, *, store_id: str, provider: str) -> EncryptedCredential:
    validate_credential_encryption_config(required=True)
    version = os.environ["CREDENTIAL_ACTIVE_KEY_VERSION"]
    key = _keyring()[version]
    nonce = os.urandom(12)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext.encode(), _aad(store_id, provider))
    return EncryptedCredential(
        ciphertext=base64.b64encode(ciphertext).decode(),
        nonce=base64.b64encode(nonce).decode(),
        key_version=version,
    )


def decrypt_credential(
    encrypted: EncryptedCredential, *, store_id: str, provider: str
) -> str:
    keys = _keyring()
    key = keys.get(encrypted.key_version)
    if key is None:
        raise CredentialDecryptionError("Credential key version is unavailable.")
    try:
        nonce = base64.b64decode(encrypted.nonce, validate=True)
        ciphertext = base64.b64decode(encrypted.ciphertext, validate=True)
        plaintext = AESGCM(key).decrypt(nonce, ciphertext, _aad(store_id, provider))
        return plaintext.decode()
    except (InvalidTag, ValueError, UnicodeDecodeError) as exc:
        raise CredentialDecryptionError("Credential authentication failed.") from exc
