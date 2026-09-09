"""Taking a token back.

Signing a token proves it was minted here. Nothing in a signature can say the
token is *still* meant to work, which is why clearing the browser was never
signing out: the credential stayed valid for the rest of its life, and a shared
computer or a lost laptop stayed an open door until the week ran out.

Every token now names a row in `token_sessions`, and these tests hold the line
that matters: revoking is a single write, and the very next request made with
that token is refused. The rest — listing, sparing the current session, never
showing a credential back — exists so that revoking is a thing somebody can
actually find and do.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import datetime as dt
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import security, sessions
from app.db import models
from app.db.models import Base
from app.db.session import get_session
from app.main import app
from app.routers import auth as auth_router

EMAIL = "seller@example.com"
OTHER = "someone-else@example.com"
CLIENT_ID = "1234.apps.googleusercontent.com"


@pytest.fixture
def api(monkeypatch):
    """A client on a private in-memory DB, with authentication switched on."""
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

    # The revocation check runs in middleware, before any dependency, so it
    # opens its own session and has to be pointed at the same database.
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


def _sign_in(client, monkeypatch, email=EMAIL) -> str:
    monkeypatch.setattr(auth_router, "verify_google_credential",
                        lambda credential: email)
    response = client.post("/api/auth/google", json={"credential": "proof"})
    assert response.status_code == 200, response.text
    return response.json()["token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --- the guarantee ----------------------------------------------------------


def test_a_revoked_session_is_refused_on_the_very_next_request(api, monkeypatch):
    client, factory = api
    token = _sign_in(client, monkeypatch)
    assert client.get("/api/auth/me", headers=_auth(token)).status_code == 200

    with factory() as db:
        row = db.scalars(select(models.TokenSession)).one()
        sessions.revoke(db, row.id)

    refused = client.get("/api/auth/me", headers=_auth(token))
    assert refused.status_code == 401
    assert "session has ended" in refused.json()["detail"].lower()


def test_signing_out_ends_this_session_and_the_token_stops_working(api, monkeypatch):
    client, _ = api
    token = _sign_in(client, monkeypatch)

    response = client.post("/api/auth/signout", headers=_auth(token))
    assert response.status_code == 200
    assert response.json()["ended"] == 1

    assert client.get("/api/auth/me", headers=_auth(token)).status_code == 401


def test_signing_out_twice_is_not_an_error_and_does_not_double_count(api, monkeypatch):
    client, _ = api
    token = _sign_in(client, monkeypatch)
    assert client.post("/api/auth/signout", headers=_auth(token)).json()["ended"] == 1
    # The second attempt cannot even authenticate, which is the correct answer.
    assert client.post("/api/auth/signout", headers=_auth(token)).status_code == 401


def test_a_token_minted_before_sessions_existed_is_refused(api):
    """No `jti` means no row, which means nothing could ever revoke it."""
    from app.issue_token import sign_token

    legacy = sign_token(user_id=str(uuid.uuid4()), tenant_id=str(uuid.uuid4()),
                        role="owner", days=1, secret="k" * 48)
    # Strip the claim the way a token from before this change would lack it.
    import base64, json, hmac, hashlib
    header, payload, _ = legacy.split(".")
    claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    claims.pop("jti")
    body = base64.urlsafe_b64encode(
        json.dumps(claims, separators=(",", ":"), sort_keys=True).encode()
    ).decode().rstrip("=")
    signature = hmac.new(b"k" * 48, f"{header}.{body}".encode(), hashlib.sha256).digest()
    forged = f"{header}.{body}.{base64.urlsafe_b64encode(signature).decode().rstrip('=')}"

    with pytest.raises(security.AuthenticationError, match="no session"):
        security.authenticate_bearer(f"Bearer {forged}")


# --- seeing and ending the others -------------------------------------------


def test_the_sessions_list_shows_live_ones_and_marks_the_current(api, monkeypatch):
    client, _ = api
    first = _sign_in(client, monkeypatch)
    second = _sign_in(client, monkeypatch)  # same account, another device

    body = client.get("/api/auth/sessions", headers=_auth(second)).json()
    assert len(body) == 2
    assert sum(1 for row in body if row["current"]) == 1
    assert all(row["method"] == "google" for row in body)
    assert first  # both are live; neither ended the other


def test_the_sessions_list_never_hands_back_a_credential(api, monkeypatch):
    client, _ = api
    token = _sign_in(client, monkeypatch)
    body = client.get("/api/auth/sessions", headers=_auth(token)).json()

    serialised = repr(body)
    assert token not in serialised
    assert "token" not in body[0]
    # The id names the session, not the token: it is safe to show and to revoke by.
    assert set(body[0]) == {"id", "method", "issued_at", "last_seen_at",
                            "expires_at", "current"}


def test_revoking_others_ends_them_and_spares_the_one_asking(api, monkeypatch):
    client, _ = api
    old_laptop = _sign_in(client, monkeypatch)
    here = _sign_in(client, monkeypatch)

    response = client.post("/api/auth/sessions/revoke-others", headers=_auth(here))
    assert response.status_code == 200
    assert response.json()["ended"] == 1

    assert client.get("/api/auth/me", headers=_auth(old_laptop)).status_code == 401
    assert client.get("/api/auth/me", headers=_auth(here)).status_code == 200


def test_revoking_others_does_not_reach_another_persons_sessions(api, monkeypatch):
    client, _ = api
    theirs = _sign_in(client, monkeypatch, email=OTHER)
    mine = _sign_in(client, monkeypatch, email=EMAIL)

    assert client.post("/api/auth/sessions/revoke-others",
                       headers=_auth(mine)).json()["ended"] == 0
    assert client.get("/api/auth/me", headers=_auth(theirs)).status_code == 200


def test_signing_in_is_recorded_in_the_audit_trail(api, monkeypatch):
    client, factory = api
    _sign_in(client, monkeypatch)

    with factory() as db:
        actions = [row.action for row in db.scalars(select(models.AuditEvent))]
    assert "auth.signed_in" in actions


# --- the unit underneath ----------------------------------------------------


def test_an_expired_session_is_not_live_even_before_it_is_swept(api):
    _, factory = api
    with factory() as db:
        past = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=1)
        row = models.TokenSession(
            id=uuid.uuid4(), user_id=uuid.uuid4(), store_id=uuid.uuid4(),
            role="owner", method="cli", issued_at=past, expires_at=past,
            last_seen_at=past)
        db.add(row)
        db.commit()
        assert sessions.live_session(db, str(row.id)) is None


def test_last_seen_is_not_written_on_every_request(api):
    """An idle tab polling the API must not cost one write per poll."""
    _, factory = api
    with factory() as db:
        now = dt.datetime.now(dt.timezone.utc)
        row = models.TokenSession(
            id=uuid.uuid4(), user_id=uuid.uuid4(), store_id=uuid.uuid4(),
            role="owner", method="cli", issued_at=now,
            expires_at=now + dt.timedelta(days=1), last_seen_at=now)
        db.add(row)
        db.commit()

        first = sessions.live_session(db, str(row.id)).last_seen_at
        second = sessions.live_session(db, str(row.id)).last_seen_at
        assert first == second

        # Once it is stale enough, the next look does update it.
        #
        # Compared against the stale value rather than against issued_at. This
        # test flaked roughly once in five full runs: the whole body can finish
        # inside a single clock tick — Windows' timer is coarse — so the
        # refreshed timestamp came back exactly equal to issued_at and a strict
        # `>` was false. Nothing was wrong with the code. The stale value is 65
        # seconds behind, so it cannot be reached by any granularity.
        stale = now - dt.timedelta(seconds=sessions.TOUCH_AFTER_SECONDS + 5)
        row.last_seen_at = stale
        db.add(row)
        db.commit()

        refreshed = sessions.live_session(db, str(row.id)).last_seen_at
        assert refreshed > stale, "a stale session should have been touched"
        assert refreshed >= row.issued_at, "and never touched backwards"


def test_a_nonsense_session_id_is_simply_not_live(api):
    _, factory = api
    with factory() as db:
        assert sessions.live_session(db, "not-a-uuid") is None
        assert sessions.live_session(db, str(uuid.uuid4())) is None


# --- signing in must survive its own bookkeeping ----------------------------
#
# A real lockout came from here. The audit write committed, then tried to read
# the row back; the commit had already ended the transaction that declared the
# tenant, so the row-level policy matched nothing and the insert that had just
# succeeded could not be refreshed. The endpoint raised — after the login code
# was consumed and the session row was committed. The account was signed in,
# the token never reached anybody, and every retry answered "wrong or expired"
# because the code was genuinely gone.


def test_a_failing_audit_write_does_not_cost_the_sign_in(api, monkeypatch):
    """The code is spent and the session exists before the trail is written.
    Refusing the token at that point strands the account; it does not undo
    anything."""
    client, factory = api

    def explode(*args, **kwargs):
        raise RuntimeError("audit table unavailable")

    monkeypatch.setattr(auth_router.crud, "append_audit_event", explode)

    token = _sign_in(client, monkeypatch)
    assert token, "a sign-in that cannot be logged is still a sign-in"
    assert client.get("/api/auth/me", headers=_auth(token)).status_code == 200

    # And the session really is on record, whatever happened to the audit row.
    with factory() as db:
        assert db.scalars(select(models.TokenSession)).all()


def test_the_audit_row_is_written_when_nothing_is_wrong(api, monkeypatch):
    """The forgiving path must not become the only path."""
    client, factory = api
    _sign_in(client, monkeypatch)

    with factory() as db:
        actions = [row.action for row in db.scalars(select(models.AuditEvent))]
    assert "auth.signed_in" in actions


def test_the_audit_write_stays_in_the_transaction_that_declared_the_tenant():
    """Guards the specific mechanism, not just the symptom.

    `declare_tenant` sets app.tenant_id for the current transaction only. If
    the audit helper is asked to commit, that transaction ends and the refresh
    afterwards runs with no tenant — which is what broke sign-in. The call has
    to flush, and the router has to commit.
    """
    import inspect
    source = inspect.getsource(auth_router._open_session)
    assert "commit=False" in source, "the audit write must not end the tenant transaction"
