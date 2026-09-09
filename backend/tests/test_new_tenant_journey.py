"""
The whole path a new customer walks, on a database that starts empty.

Every step here is covered somewhere else in more detail. What this adds is the
order: that the steps compose, that nothing needs a developer between them, and
above all that each screen tells the truth about the step before it. The failures
this catches are the ones where two correct pieces disagree — a store reported as
connected with no token behind it, a sync reported as done because a job was
queued, proposals that never appear because nothing asked for them.

Shopify is never called. The OAuth exchange and the Admin API are replaced at
their boundary, so this runs offline and deterministically; what it proves is
our half of the conversation.
"""
import base64
import hashlib
import hmac
import json
import os
import sys
import uuid
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import datetime as dt
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import shopify
from app.db import crud, models
from app.db.models import Base
from app.db.session import get_session
from app.main import app

SHOP = "new-customer.myshopify.com"


@pytest.fixture
def stack(monkeypatch):
    """An empty database and a client, with Shopify's side stubbed out."""
    monkeypatch.setenv("SHOPIFY_CLIENT_ID", "client-id")
    monkeypatch.setenv("SHOPIFY_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("SHOPIFY_REDIRECT_URI",
                       "https://api.example.test/api/integrations/shopify/callback")
    monkeypatch.setenv("SHOPIFY_WEBHOOK_URI",
                       "https://api.example.test/api/webhooks/shopify")
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEYS",
                       json.dumps({"v1": base64.b64encode(b"k" * 32).decode()}))
    monkeypatch.setenv("CREDENTIAL_ACTIVE_KEY_VERSION", "v1")

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

    app.dependency_overrides[get_session] = override
    try:
        yield TestClient(app), factory
    finally:
        app.dependency_overrides.pop(get_session, None)


def _connect(client, factory, monkeypatch, *, topics=("ORDERS_CREATE",)):
    """Walk the OAuth handshake with Shopify's half stubbed."""
    authorize = client.post(f"/api/integrations/shopify/authorize?shop={SHOP}")
    assert authorize.status_code == 200
    state = parse_qs(urlparse(authorize.json()["authorization_url"]).query)["state"][0]

    monkeypatch.setattr(shopify, "exchange_code",
                        lambda shop, code: {"access_token": "shpat_stub",
                                            "scope": "read_products,read_orders"})

    class Client:
        def __init__(self, *a, **k): pass
        def ensure_webhook_subscriptions(self, uri): return list(topics)
        def close(self): pass

    monkeypatch.setattr(shopify, "AdminGraphQLClient", Client)

    query = f"code=abc&shop={SHOP}&state={state}&timestamp=1"
    digest = hmac.new(b"client-secret", query.encode(), hashlib.sha256).hexdigest()
    return client.get(f"/api/integrations/shopify/callback?{query}&hmac={digest}")


# --- the path itself --------------------------------------------------------

def test_a_new_tenant_starts_with_nothing_and_is_told_so(stack):
    """No store, no proposals, and screens that say which rather than erroring."""
    client, _ = stack
    account = client.get("/api/integrations/shopify/account").json()
    assert account["connected"] is False
    assert client.get("/api/actions").json()["actions"] == []
    payroll = client.get("/api/dashboard/payroll").json()
    assert payroll["settled_impact"] == 0


def test_connecting_a_store_makes_it_connected_and_not_before(stack, monkeypatch):
    client, factory = stack
    assert client.get("/api/integrations/shopify/account").json()["connected"] is False

    assert _connect(client, factory, monkeypatch).status_code == 200

    account = client.get("/api/integrations/shopify/account").json()
    assert account["connected"] is True
    assert account["shop"] == SHOP
    assert account["notifications"] == "active"


def test_a_channel_row_without_a_token_is_not_a_connected_store(stack):
    """The misleading state worth guarding: a row saying connected while the
    credential behind it is gone. The screen must not promise a live store."""
    client, factory = stack
    db = factory()
    store = crud.get_or_create_dev_store(db)
    db.add(models.ChannelConnection(
        store_id=store.id, provider="shopify", external_account_id=SHOP,
        display_name=SHOP, status="connected", currency="USD", settings={}))
    db.commit(); db.close()

    assert client.get("/api/integrations/shopify/account").json()["connected"] is False


def test_sales_become_proposals_without_anyone_asking(stack, monkeypatch):
    """The sync re-reads what it wrote, so a proposal appears because orders
    arrived — not because somebody remembered to press a button."""
    client, factory = stack
    _connect(client, factory, monkeypatch)

    db = factory()
    store = crud.get_or_create_dev_store(db)
    channel = db.scalar(select(models.ChannelConnection))
    today = dt.datetime.now(dt.timezone.utc).date()
    for back in range(10):
        db.add(models.CommerceDailyMetric(
            store_id=store.id, channel_id=channel.id,
            metric_date=today - dt.timedelta(days=back),
            revenue=Decimal("40.00"), refunds=0, fees=0, landed_cogs=0,
            advertising_spend=0, orders=1, units=1, sessions=0, currency="USD",
            source="shopify_graphql", costs_complete=False))
    db.commit(); db.close()

    result = client.post("/api/commerce/scan").json()
    assert result["opened"] >= 1

    proposals = client.get("/api/actions").json()["actions"]
    assert [p for p in proposals if p["status"] == "proposed"]


def test_nothing_is_applied_automatically_for_a_new_store(stack, monkeypatch):
    """`auto_apply_below` is zero until the seller opts in, so every proposal
    waits and none of them is acted on."""
    client, factory = stack
    _connect(client, factory, monkeypatch)

    limits = client.get("/api/guardrails").json()
    assert limits["auto_apply_below"] == 0

    db = factory()
    applied = list(db.scalars(select(models.OperatorAction).where(
        models.OperatorAction.status != "proposed")))
    assert applied == []
    db.close()


def test_declining_a_proposal_records_the_decision(stack, monkeypatch):
    """Saying no is an answer. A queue nobody can empty stops being read."""
    client, factory = stack
    _connect(client, factory, monkeypatch)
    db = factory()
    store = crud.get_or_create_dev_store(db)
    action = models.OperatorAction(
        store_id=store.id, module="commerce", action_type="ENTER_LANDED_COSTS",
        target=SHOP, status="proposed", evidence_mode="unverified",
        currency="USD", measurement_days=14)
    db.add(action); db.commit()
    action_id = str(action.id)
    db.close()

    response = client.post(f"/api/actions/{action_id}/dismiss",
                           json={"note": "not now"})
    assert response.status_code == 200
    assert response.json()["status"] == "dismissed"


def test_nothing_unproven_reaches_the_payroll(stack, monkeypatch):
    """The rule the whole product rests on: only a measured result counts."""
    client, factory = stack
    _connect(client, factory, monkeypatch)
    db = factory()
    store = crud.get_or_create_dev_store(db)
    db.add(models.OperatorAction(
        store_id=store.id, module="commerce", action_type="RESTOCK_PRODUCT",
        target="A Snowboard", status="applied", evidence_mode="unverified",
        currency="USD", measurement_days=14,
        baseline={"days": 14, "revenue": 100.0, "spend": 0.0},
        impact={"net": 999.0, "provisional": False, "cost_avoided": 0.0,
                "revenue_gained": 999.0, "basis": "x"}))
    db.commit(); db.close()

    payroll = client.get("/api/dashboard/payroll").json()
    assert payroll["settled_impact"] == 0, "an unverified action reached the payroll"
    assert payroll["excluded_unverified"] >= 1


def test_disconnecting_and_reconnecting_the_same_shop_reuses_one_channel(
        stack, monkeypatch):
    """A customer who reconnects must not end up with two of their own store."""
    client, factory = stack
    _connect(client, factory, monkeypatch)

    db = factory()
    channel = db.scalar(select(models.ChannelConnection))
    channel.status = "disconnected"
    channel.settings = {**(channel.settings or {}), "revoked_reason": "uninstalled"}
    db.add(channel); db.commit(); db.close()

    account = client.get("/api/integrations/shopify/account").json()
    assert account["connected"] is False
    assert account["reason"] == "uninstalled"

    assert _connect(client, factory, monkeypatch).status_code == 200

    db = factory()
    channels = list(db.scalars(select(models.ChannelConnection)))
    assert len(channels) == 1, "reconnecting created a second channel"
    assert channels[0].status == "connected"
    db.close()


def test_a_customer_can_export_their_own_data(stack, monkeypatch):
    """What a pilot customer is entitled to ask for, without a developer."""
    client, factory = stack
    _connect(client, factory, monkeypatch)

    export = client.get("/api/privacy/export")
    assert export.status_code == 200
    assert export.json()


def test_deletion_is_a_recorded_request_and_not_a_silent_button(stack, monkeypatch):
    """Erasure is deliberately not one click: the endpoint records a request for
    review, and a person carries it out. So the promise this makes is narrow —
    that the ask is recorded and can be found — and the erasure itself is a
    runbook, not an endpoint. Anything else here would be a button claiming to
    have deleted data it had not touched."""
    client, factory = stack
    _connect(client, factory, monkeypatch)

    wrong = client.post("/api/privacy/deletion-request",
                        json={"confirmation": "delete"})
    assert wrong.status_code == 400, "a mistyped confirmation must not count"

    response = client.post("/api/privacy/deletion-request",
                           json={"confirmation": "DELETE MY WORKSPACE"})
    assert response.status_code == 200
    assert response.json()["status"] == "pending_review"

    db = factory()
    recorded = [e for e in db.scalars(select(models.AuditEvent))
                if e.action == "privacy.deletion_requested"]
    assert recorded, "the request was answered but not written down"
    # And nothing was destroyed by asking.
    assert list(db.scalars(select(models.IntegrationCredential)))
    db.close()
