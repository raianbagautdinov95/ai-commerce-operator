"""The four endpoints that answer without a token, and what they hand out.

These are the only unauthenticated routes in the application, so the tests care
about two things in particular: that the door opens for a proof and stays shut
otherwise, and that what comes out of it is a token the *existing* verifier
accepts. Signing and verification already live in two places; a login that
minted something slightly different would make it three.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import security
from app.db import models
from app.db.models import Base
from app.db.session import get_session
from app.main import app
from app.routers import auth as auth_router

EMAIL = "seller@example.com"
CLIENT_ID = "1234.apps.googleusercontent.com"


@pytest.fixture
def api(monkeypatch):
    """A client on a private in-memory DB, with authentication switched on.

    The previous override is restored rather than cleared: other test modules
    install their own at import time, and clearing would silently break them.
    """
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("JWT_SECRET", "k" * 48)
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.delenv("JWT_ISSUER", raising=False)
    monkeypatch.delenv("JWT_AUDIENCE", raising=False)
    monkeypatch.setenv("GOOGLE_CLIENT_ID", CLIENT_ID)

    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    def override():
        session = factory()
        try:
            yield session
        finally:
            session.close()

    # The middleware checks whether a token's session has been revoked, and it
    # runs before any dependency does, so it opens its own session. Point that
    # at the same in-memory database or every authenticated call in this module
    # fails against the real dev file.
    monkeypatch.setattr("app.db.session.SessionLocal", factory)

    previous = app.dependency_overrides.get(get_session)
    app.dependency_overrides[get_session] = override
    try:
        yield TestClient(app), factory
    finally:
        if previous is None:
            app.dependency_overrides.pop(get_session, None)
        else:
            app.dependency_overrides[get_session] = previous


@pytest.fixture
def sent(monkeypatch):
    """Intercept delivery so the test can read the code the seller would get."""
    box: list[tuple[str, str]] = []
    monkeypatch.setattr(auth_router, "send_login_code",
                        lambda email, code: box.append((email, code)))
    return box


def _accept_google(monkeypatch, email=EMAIL):
    monkeypatch.setattr(auth_router, "verify_google_credential",
                        lambda credential: email)


def test_the_sign_in_screen_can_ask_what_is_on_offer(api):
    client, _ = api
    body = client.get("/api/auth/config").json()
    assert body["google_client_id"] == CLIENT_ID
    assert body["session_days"] >= 1


def test_a_google_sign_in_returns_a_token_the_verifier_accepts(api, monkeypatch):
    client, _ = api
    _accept_google(monkeypatch)
    body = client.post("/api/auth/google", json={"credential": "whatever"}).json()

    principal = security.authenticate_bearer(f"Bearer {body['token']}")
    assert principal.role == "owner"
    assert principal.tenant_id == body["tenant_id"]
    assert body["email"] == EMAIL


def test_a_rejected_google_credential_is_a_401_and_creates_nothing(api, monkeypatch):
    client, factory = api
    monkeypatch.setattr(
        auth_router, "verify_google_credential",
        lambda credential: (_ for _ in ()).throw(
            auth_router.GoogleIdentityError("Google could not confirm this sign-in.")))
    response = client.post("/api/auth/google", json={"credential": "forged"})
    assert response.status_code == 401
    with factory() as db:
        assert db.scalars(select(models.User)).all() == []


def test_signing_in_twice_keeps_one_tenant(api, monkeypatch):
    client, factory = api
    _accept_google(monkeypatch)
    first = client.post("/api/auth/google", json={"credential": "a"}).json()
    second = client.post("/api/auth/google", json={"credential": "b"}).json()
    assert first["tenant_id"] == second["tenant_id"]
    with factory() as db:
        assert len(db.scalars(select(models.Store)).all()) == 1


def test_google_and_an_email_code_land_on_the_same_account(api, monkeypatch, sent):
    """The reason `ensure_account` exists: two doors, one tenant."""
    client, _ = api
    _accept_google(monkeypatch)
    by_google = client.post("/api/auth/google", json={"credential": "a"}).json()

    client.post("/api/auth/email/request", json={"email": EMAIL})
    _, code = sent[-1]
    by_email = client.post("/api/auth/email/verify",
                           json={"email": EMAIL, "code": code}).json()
    assert by_email["tenant_id"] == by_google["tenant_id"]


def test_the_email_code_round_trip(api, sent):
    client, _ = api
    assert client.post("/api/auth/email/request", json={"email": EMAIL}).status_code == 202
    address, code = sent[-1]
    assert address == EMAIL

    body = client.post("/api/auth/email/verify",
                       json={"email": EMAIL, "code": code}).json()
    assert security.authenticate_bearer(f"Bearer {body['token']}").role == "owner"


def test_a_wrong_code_is_refused_without_saying_why(api, sent):
    client, _ = api
    client.post("/api/auth/email/request", json={"email": EMAIL})
    _, code = sent[-1]
    wrong = "000000" if code != "000000" else "111111"
    response = client.post("/api/auth/email/verify", json={"email": EMAIL, "code": wrong})
    assert response.status_code == 400
    assert "wrong or has expired" in response.json()["detail"]


def test_asking_for_a_code_never_reveals_whether_the_address_is_known(api, sent):
    client, _ = api
    known = client.post("/api/auth/email/request", json={"email": EMAIL})
    stranger = client.post("/api/auth/email/request", json={"email": "nobody@example.com"})
    assert known.status_code == stranger.status_code == 202
    assert known.json() == stranger.json()


def test_the_token_from_a_login_opens_the_rest_of_the_api(api, monkeypatch):
    client, _ = api
    _accept_google(monkeypatch)
    token = client.post("/api/auth/google", json={"credential": "a"}).json()["token"]

    assert client.get("/api/auth/me").status_code == 401
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["email"] == EMAIL
    assert me.json()["role"] == "owner"


def test_production_refuses_to_write_sign_in_codes_to_the_log(api, monkeypatch):
    """The dev fallback is the one that must never reach a real deployment."""
    client, _ = api
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.delenv("LOGIN_EMAIL_FROM", raising=False)
    response = client.post("/api/auth/email/request", json={"email": EMAIL})
    assert response.status_code == 503
    assert "RESEND_API_KEY" in response.json()["detail"]


def test_the_public_paths_are_exactly_the_ones_that_should_be(api):
    """A route added to that set by accident is an unauthenticated endpoint."""
    from app.main import PUBLIC_PATHS
    assert PUBLIC_PATHS >= {"/api/auth/config", "/api/auth/google",
                            "/api/auth/email/request", "/api/auth/email/verify"}
    assert "/api/auth/me" not in PUBLIC_PATHS
    assert not any(path.startswith("/api/") and "auth" not in path and
                   "callback" not in path and "webhook" not in path and
                   path != "/api/legal" for path in PUBLIC_PATHS)
