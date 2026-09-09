"""
What happens when a merchant uninstalls the app, or the token is revoked.

The failure this guards against is quiet: the sync keeps failing, RQ keeps
retrying, the UI keeps saying "connected", and nobody is asked to reconnect. A
revoked token does not un-revoke, so the only useful reaction is to stop, say so,
and drop the dead credential.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import base64
import json

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import credentials, shopify, tasks
from app.db import crud, models
from app.db.models import Base

SHOP = "revoked-shop.myshopify.com"


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("SHOPIFY_CLIENT_ID", "shopify-client")
    monkeypatch.setenv("SHOPIFY_CLIENT_SECRET", "shopify-secret")
    monkeypatch.setenv("SHOPIFY_REDIRECT_URI", "https://app.test/cb")
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEYS",
                       json.dumps({"v1": base64.b64encode(b"r" * 32).decode()}))
    monkeypatch.setenv("CREDENTIAL_ACTIVE_KEY_VERSION", "v1")

    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(tasks, "SessionLocal", factory)

    db = factory()
    store = crud.get_or_create_dev_store(db)
    channel = models.ChannelConnection(
        store_id=store.id, provider="shopify", external_account_id=SHOP,
        display_name="Revoked Shop", status="connected", currency="USD", settings={})
    db.add(channel); db.commit(); db.refresh(channel)
    credentials.store_credential(db, store_id=store.id,
                                 provider=shopify.credential_provider(SHOP),
                                 secret="shpat_revoked")
    record = models.IdempotencyRecord(store_id=store.id, operation="shopify.sync",
                                      key="k-1", status="processing")
    db.add(record); db.commit()
    ids = (str(store.id), str(store.user_id), str(channel.id), str(record.id))
    db.close()
    factory.ids = ids
    return factory


def _sync(factory):
    store_id, actor_id, channel_id, record_id = factory.ids
    return tasks.run_shopify_sync(tenant_id=store_id, actor_id=actor_id,
                                  channel_id=channel_id, days=7,
                                  idempotency_record_id=record_id)


def _respond(monkeypatch, status):
    def handler(request):
        return httpx.Response(status, json={"errors": [{"message": "denied"}]})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    real = shopify.AdminGraphQLClient          # capture before replacing it
    monkeypatch.setattr(shopify, "AdminGraphQLClient",
                        lambda shop, token, **kw: real(shop, token, client=client))
    return client


@pytest.mark.parametrize("status", [401, 403])
def test_a_revoked_token_disconnects_the_channel_instead_of_retrying(env, monkeypatch, status):
    _respond(monkeypatch, status)
    result = _sync(env)

    assert result["status"] == "revoked"
    assert "uninstalled or its access revoked" in result["reason"]

    db = env()
    channel = db.scalar(select(models.ChannelConnection))
    assert channel.status == "disconnected"
    assert channel.settings["revoked_at"]
    assert "revoked" in channel.settings["revoked_reason"]
    db.close()


def test_the_dead_credential_is_dropped(env, monkeypatch):
    """A token Shopify has revoked is not a secret worth keeping, only a liability."""
    _respond(monkeypatch, 401)
    _sync(env)

    db = env()
    store = crud.get_or_create_dev_store(db)
    assert credentials.load_credential(
        db, store_id=store.id, provider=shopify.credential_provider(SHOP)) is None
    db.close()


def test_revocation_is_audited_so_the_reason_survives(env, monkeypatch):
    _respond(monkeypatch, 401)
    _sync(env)

    db = env()
    actions = [row.action for row in db.scalars(select(models.AuditEvent))]
    assert "shopify.access_revoked" in actions
    db.close()


def test_the_job_does_not_raise_so_rq_does_not_retry_a_locked_door(env, monkeypatch):
    """Raising would burn three retries over twelve minutes to learn the same thing."""
    _respond(monkeypatch, 401)
    result = _sync(env)                      # would raise if we treated it as transient
    assert result["orders"] == 0

    db = env()
    record = db.scalar(select(models.IdempotencyRecord))
    assert record.status == "completed"      # settled, not left half-done
    db.close()


def test_a_server_error_is_still_transient_and_still_raises(env, monkeypatch):
    """Only 401/403 mean 'stop'. A 500 is Shopify having a bad minute."""
    _respond(monkeypatch, 500)
    with pytest.raises(shopify.ShopifyAPIError):
        _sync(env)

    db = env()
    channel = db.scalar(select(models.ChannelConnection))
    assert channel.status == "connected"     # not disconnected over a blip
    store = crud.get_or_create_dev_store(db)
    assert credentials.load_credential(
        db, store_id=store.id, provider=shopify.credential_provider(SHOP)) is not None
    db.close()


def test_the_screen_explains_a_revoked_connection_instead_of_forgetting_it(env, monkeypatch):
    """"Connect a store" with no explanation leaves the merchant guessing."""
    from fastapi.testclient import TestClient
    from app.db.session import get_session
    from app.main import app

    _respond(monkeypatch, 401)
    _sync(env)

    def override():
        session = env()
        try:
            yield session
        finally:
            session.close()

    previous = app.dependency_overrides.get(get_session)
    app.dependency_overrides[get_session] = override
    try:
        body = TestClient(app).get("/api/integrations/shopify/account").json()
    finally:
        if previous is None:
            app.dependency_overrides.pop(get_session, None)
        else:
            app.dependency_overrides[get_session] = previous

    assert body["connected"] is False
    assert body["shop"] == SHOP
    assert "revoked" in body["reason"]
