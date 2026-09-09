"""
The issued token must be accepted by the code that verifies it.

Signing and verification are two halves of one contract written in two places,
so these tests always go through `authenticate_bearer` rather than re-checking
the signature by hand — otherwise both halves could drift together and agree on
something the API rejects.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import time
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import security
from app.db.models import Base
from app.issue_token import (MAX_DAYS, TokenConfigurationError, ensure_operator,
                             sign_token)

SECRET = "j" * 48
USER = str(uuid.uuid4())
TENANT = str(uuid.uuid4())


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", SECRET)
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.delenv("JWT_ISSUER", raising=False)
    monkeypatch.delenv("JWT_AUDIENCE", raising=False)


def _principal(token):
    return security.authenticate_bearer(f"Bearer {token}")


def test_an_issued_token_is_accepted_by_the_verifier(env):
    token = sign_token(user_id=USER, tenant_id=TENANT, role="owner", days=30)
    principal = _principal(token)
    assert principal.user_id == USER
    assert principal.tenant_id == TENANT
    assert principal.role == "owner"


def test_the_role_in_the_token_is_the_role_that_is_enforced(env):
    viewer = _principal(sign_token(user_id=USER, tenant_id=TENANT, role="viewer", days=1))
    security.require_role(viewer, "viewer")
    with pytest.raises(security.AuthorizationError):
        security.require_role(viewer, "admin")

    owner = _principal(sign_token(user_id=USER, tenant_id=TENANT, role="owner", days=1))
    security.require_role(owner, "owner")


def test_issuer_and_audience_are_carried_when_configured(env, monkeypatch):
    monkeypatch.setenv("JWT_ISSUER", "ai-commerce-operator")
    monkeypatch.setenv("JWT_AUDIENCE", "ai-commerce-operator")
    token = sign_token(user_id=USER, tenant_id=TENANT, role="owner", days=1)
    assert _principal(token).role == "owner"

    # A token minted for a different deployment must not be accepted here.
    monkeypatch.setenv("JWT_AUDIENCE", "somewhere-else")
    with pytest.raises(security.AuthenticationError):
        _principal(token)


def test_an_expired_token_is_refused(env):
    token = sign_token(user_id=USER, tenant_id=TENANT, role="owner", days=1,
                       now=int(time.time()) - (2 * 86400))
    with pytest.raises(security.AuthenticationError):
        _principal(token)


def test_a_token_signed_with_another_secret_is_refused(env):
    token = sign_token(user_id=USER, tenant_id=TENANT, role="owner", days=1,
                       secret="different" * 8)
    with pytest.raises(security.AuthenticationError):
        _principal(token)


def test_rotating_the_secret_invalidates_every_token(env, monkeypatch):
    """The only revocation there is, so it had better work."""
    token = sign_token(user_id=USER, tenant_id=TENANT, role="owner", days=30)
    assert _principal(token).role == "owner"
    monkeypatch.setenv("JWT_SECRET", "rotated" * 8)
    with pytest.raises(security.AuthenticationError):
        _principal(token)


def test_a_weak_secret_refuses_to_sign_anything(env, monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "short")
    with pytest.raises(TokenConfigurationError) as exc:
        sign_token(user_id=USER, tenant_id=TENANT, role="owner", days=1)
    assert "32 bytes" in str(exc.value)


def test_an_unknown_role_is_refused_before_a_token_exists(env):
    with pytest.raises(TokenConfigurationError) as exc:
        sign_token(user_id=USER, tenant_id=TENANT, role="superuser", days=1)
    assert "Unknown role" in str(exc.value)


def test_an_absurd_lifetime_is_refused(env):
    for days in (0, -1, MAX_DAYS + 1):
        with pytest.raises(TokenConfigurationError):
            sign_token(user_id=USER, tenant_id=TENANT, role="owner", days=days)


def test_the_operator_and_their_store_are_created_once_and_reused():
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()

    user, store = ensure_operator(db, email="Owner@Example.com ")
    assert user.email == "owner@example.com"        # normalised
    again, same_store = ensure_operator(db, email="owner@example.com")
    assert again.id == user.id and same_store.id == store.id

    other, other_store = ensure_operator(db, email="second@example.com")
    assert other.id != user.id
    assert other_store.id != store.id               # a tenant of their own
    db.close()
