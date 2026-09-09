"""What Sign in with Google must refuse.

The library checks the signature, the expiry and the audience, and those are
tested by the people who wrote it. What is tested here is the part this codebase
owns: which verified claims are allowed to become an account, and which are not.

The one that would hurt is `email_verified`. `ensure_account` keys an account on
an email address, so accepting an unverified one lets anybody who can get Google
to mint a token for an address they do not control walk into that address's
tenant.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.accounts import ensure_account
from app.auth_google import (GoogleIdentityError, google_enabled,
                             verify_google_credential)
from app.db.models import Base

CLIENT_ID = "1234.apps.googleusercontent.com"


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", CLIENT_ID)


def _claims(**overrides):
    base = {
        "iss": "https://accounts.google.com",
        "aud": CLIENT_ID,
        "email": "seller@example.com",
        "email_verified": True,
    }
    base.update(overrides)
    return base


def _verifier(claims):
    return lambda credential, client_id: claims


def test_a_verified_google_account_yields_its_address(configured):
    email = verify_google_credential("token", verifier=_verifier(_claims()))
    assert email == "seller@example.com"


def test_google_sends_email_verified_as_a_string_in_some_flows(configured):
    email = verify_google_credential(
        "token", verifier=_verifier(_claims(email_verified="true")))
    assert email == "seller@example.com"


def test_an_unverified_address_is_refused(configured):
    """The whole reason this module exists rather than trusting the claim set."""
    for value in (False, "false", None, "", 0):
        with pytest.raises(GoogleIdentityError) as exc:
            verify_google_credential("token",
                                     verifier=_verifier(_claims(email_verified=value)))
        assert "verified" in str(exc.value).lower()


def test_a_token_for_another_application_is_refused(configured):
    with pytest.raises(GoogleIdentityError):
        verify_google_credential("token",
                                 verifier=_verifier(_claims(aud="9999.apps.googleusercontent.com")))


def test_a_token_from_another_issuer_is_refused(configured):
    with pytest.raises(GoogleIdentityError):
        verify_google_credential("token",
                                 verifier=_verifier(_claims(iss="accounts.evil.example")))


def test_a_claim_set_without_an_email_is_refused(configured):
    claims = _claims()
    del claims["email"]
    with pytest.raises(GoogleIdentityError):
        verify_google_credential("token", verifier=_verifier(claims))


def test_an_empty_credential_is_refused_before_anything_is_verified(configured):
    with pytest.raises(GoogleIdentityError):
        verify_google_credential("   ", verifier=_verifier(_claims()))


def test_google_is_off_until_a_client_id_is_configured(monkeypatch):
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    assert google_enabled() is False
    with pytest.raises(GoogleIdentityError) as exc:
        verify_google_credential("token", verifier=_verifier(_claims()))
    assert "not configured" in str(exc.value)


def test_the_same_person_through_two_doors_gets_one_tenant():
    """A Shopify install and a Google sign-in must not produce two stores."""
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()

    user, store = ensure_account(db, email="Seller@Example.com")
    again, same = ensure_account(db, email=" seller@example.com ")
    assert again.id == user.id and same.id == store.id

    other, other_store = ensure_account(db, email="someone.else@example.com")
    assert other.id != user.id and other_store.id != store.id
    db.close()


def test_a_gmail_dotted_alias_is_a_different_account_on_purpose():
    """Gmail treats dots as noise; a Workspace domain does not. We never guess."""
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    first, _ = ensure_account(db, email="john.smith@example.com")
    second, _ = ensure_account(db, email="johnsmith@example.com")
    assert first.id != second.id
    db.close()
