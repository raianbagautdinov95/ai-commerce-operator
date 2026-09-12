"""
Tests for the onboarding checklist.

The failure being guarded against is a checklist that flatters. Tick a step when
somebody presses the button that starts it and you get "Shopify connected" over
a store whose token was never stored, and "synced" over a job that failed — which
is worse than no checklist, because the customer stops looking for the thing
that is wrong.

So most of these are about a step refusing to say complete.
"""
import base64
import datetime as dt
import json
import os
import sys
import uuid
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import commerce_service, credentials, onboarding, shopify
from app.db import crud, models
from app.db.models import Base

SHOP = "onboarding-test.myshopify.com"


@pytest.fixture(autouse=True)
def _keys(monkeypatch):
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEYS",
                       json.dumps({"v1": base64.b64encode(b"k" * 32).decode()}))
    monkeypatch.setenv("CREDENTIAL_ACTIVE_KEY_VERSION", "v1")
    monkeypatch.setenv("SHOPIFY_WEBHOOK_URI", "https://api.example.test/api/webhooks/shopify")


@pytest.fixture
def db():
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    yield session
    session.close()


def _store(db, email="a@example.test"):
    user = models.User(email=email)
    db.add(user); db.flush()
    store = models.Store(user_id=user.id, marketplace="US")
    db.add(store); db.commit()
    return store


def _connect(db, store, *, topics=("ORDERS_CREATE",), token=True,
             uri="https://api.example.test/api/webhooks/shopify", shop=SHOP):
    channel = models.ChannelConnection(
        store_id=store.id, provider="shopify", external_account_id=shop,
        display_name=shop, status="connected", currency="USD",
        settings={"webhooks": list(topics), "webhook_uri": uri,
                  "scopes": ["read_products", "read_orders"]})
    db.add(channel); db.flush()
    if token:
        credentials.store_credential(
            db, store_id=store.id, provider=shopify.credential_provider(shop),
            secret="shpat_stub")
    db.commit()
    return channel


def _step(db, store, key):
    return {s.key: s for s in onboarding.status(db, store=store).steps}[key]


# --- an empty account is honest about being empty ---------------------------

def test_a_brand_new_account_has_started_nothing(db):
    store = _store(db)
    steps = {s.key: s.status for s in onboarding.status(db, store=store).steps}
    assert steps["oauth"] == onboarding.NOT_STARTED
    assert steps["sync"] == onboarding.NOT_STARTED
    assert steps["analysis"] == onboarding.NOT_STARTED
    assert steps["account"] == onboarding.COMPLETE


def test_the_read_only_promise_is_stated_before_connecting(db):
    """It is what the customer is agreeing to, so it is said first and as a
    fact about the scopes rather than as reassurance."""
    store = _store(db)
    step = _step(db, store, "permissions")
    assert step.status == onboarding.COMPLETE
    assert "change anything" in step.detail


# --- a row is not a connection ----------------------------------------------

def test_a_channel_without_a_token_does_not_complete_the_token_step(db):
    """The exact lie this checklist exists to prevent."""
    store = _store(db)
    _connect(db, store, token=False)
    step = _step(db, store, "token")
    assert step.status == onboarding.NEEDS_ATTENTION
    assert step.action == "connect_shopify"


def test_a_connected_shop_with_a_token_completes_both_steps(db):
    store = _store(db)
    _connect(db, store)
    assert _step(db, store, "oauth").status == onboarding.COMPLETE
    assert _step(db, store, "token").status == onboarding.COMPLETE


def test_a_revoked_connection_asks_to_reconnect(db):
    store = _store(db)
    channel = _connect(db, store)
    channel.status = "disconnected"
    channel.settings = {**channel.settings, "revoked_reason": "You uninstalled the app."}
    db.add(channel); db.commit()

    step = _step(db, store, "oauth")
    assert step.status == onboarding.NEEDS_ATTENTION
    assert step.detail == "You uninstalled the app."
    assert step.action == "connect_shopify"


# --- connected and broken are different claims ------------------------------

def test_refused_deliveries_are_not_hidden_behind_a_green_connection(db, monkeypatch):
    """A shop can be connected and not working at the same time. A checklist
    that cannot say both will say the flattering one."""
    monkeypatch.setattr(onboarding.webhook_health, "signatures_failing", lambda: True)
    store = _store(db)
    _connect(db, store)

    assert _step(db, store, "oauth").status == onboarding.COMPLETE
    step = _step(db, store, "notifications")
    assert step.status == onboarding.NEEDS_ATTENTION
    assert step.help_url == "/help/failing-signature"


def test_subscriptions_pointing_elsewhere_need_attention(db):
    store = _store(db)
    _connect(db, store, uri="https://yesterdays-tunnel.example/api/webhooks/shopify")
    step = _step(db, store, "notifications")
    assert step.status == onboarding.NEEDS_ATTENTION
    assert step.action == "retry_notifications"


def test_subscribing_and_receiving_are_reported_separately(db):
    """One says Shopify agreed to tell us; the other says it has."""
    store = _store(db)
    _connect(db, store)
    assert _step(db, store, "notifications").status == onboarding.COMPLETE
    assert _step(db, store, "delivery").status == onboarding.IN_PROGRESS


def test_a_verified_delivery_completes_the_delivery_step(db):
    store = _store(db)
    channel = _connect(db, store)
    db.add(models.WebhookDelivery(
        store_id=store.id, provider="shopify", delivery_id="d1",
        topic="orders/create", payload_hash="x", status="completed",
        received_at=dt.datetime.now(dt.timezone.utc)))
    db.commit()
    assert _step(db, store, "delivery").status == onboarding.COMPLETE


# --- sync and analysis are not the same step --------------------------------

def _synced(db, store, channel):
    db.add(models.CommerceDailyMetric(
        store_id=store.id, channel_id=channel.id,
        metric_date=dt.datetime.now(dt.timezone.utc).date(),
        revenue=Decimal("40.00"), refunds=0, fees=0, landed_cogs=0,
        advertising_spend=0, orders=1, units=1, sessions=0, currency="USD",
        source="shopify_graphql", costs_complete=False,
        synced_at=dt.datetime.now(dt.timezone.utc)))
    db.commit()


def test_a_finished_sync_with_no_analysis_offers_only_the_analysis(db):
    """The customer must not be told to reconnect Shopify because a follow-up
    step did not run. Reconnecting fixes nothing and costs them the token."""
    store = _store(db)
    channel = _connect(db, store)
    _synced(db, store, channel)

    assert _step(db, store, "sync").status == onboarding.COMPLETE
    analysis = _step(db, store, "analysis")
    assert analysis.status == onboarding.NEEDS_ATTENTION
    assert analysis.action == "run_analysis"


def test_an_analysis_that_found_nothing_is_complete_and_says_why(db):
    """No proposals is an answer. Presented as a failure it reads as a broken
    product; presented as nothing at all it reads as one that did not run."""
    store = _store(db)
    channel = _connect(db, store)
    _synced(db, store, channel)
    result = commerce_service.scan_store(db, store)
    assert result["opened"] == 0

    step = _step(db, store, "analysis")
    assert step.status == onboarding.COMPLETE
    assert "not a failure" in step.detail


def test_an_analysis_with_proposals_points_at_them(db):
    store = _store(db)
    channel = _connect(db, store)
    _synced(db, store, channel)
    db.add(models.OperatorAction(
        store_id=store.id, module="commerce", action_type="ENTER_LANDED_COSTS",
        target=SHOP, status="proposed", evidence_mode="unverified",
        currency="USD", measurement_days=14))
    db.commit()

    step = _step(db, store, "analysis")
    assert step.status == onboarding.COMPLETE
    assert step.action == "open_proposals"


def test_a_failed_sync_offers_a_safe_retry(db):
    store = _store(db)
    _connect(db, store)
    db.add(models.IdempotencyRecord(
        store_id=store.id, operation="shopify.sync:1:30", key="k1", status="failed"))
    db.commit()

    step = _step(db, store, "sync")
    assert step.status == onboarding.NEEDS_ATTENTION
    assert step.action == "run_sync"
    assert "safe" in step.detail


def test_a_running_sync_is_in_progress_not_finished(db):
    store = _store(db)
    _connect(db, store)
    db.add(models.IdempotencyRecord(
        store_id=store.id, operation="shopify.sync:1:30", key="k2",
        status="processing"))
    db.commit()
    assert _step(db, store, "sync").status == onboarding.IN_PROGRESS


# --- nothing of anybody else's ----------------------------------------------

def test_one_tenants_checklist_cannot_be_completed_by_another(db):
    """Two customers, one connected. The other's checklist must not move, and
    must not name a shop that is not theirs."""
    mine = _store(db, "mine@example.test")
    theirs = _store(db, "theirs@example.test")
    _connect(db, theirs, shop="not-mine.myshopify.com")

    steps = {s.key: s for s in onboarding.status(db, store=mine).steps}
    assert steps["oauth"].status == onboarding.NOT_STARTED
    assert "not-mine" not in json.dumps([s.to_dict() for s in
                                         onboarding.status(db, store=mine).steps])


def test_another_tenants_delivery_does_not_complete_my_step(db):
    mine = _store(db, "mine2@example.test")
    theirs = _store(db, "theirs2@example.test")
    _connect(db, mine)
    channel = _connect(db, theirs, shop="other.myshopify.com")
    db.add(models.WebhookDelivery(
        store_id=theirs.id, provider="shopify", delivery_id="d-other",
        topic="orders/create", payload_hash="x", status="completed",
        received_at=dt.datetime.now(dt.timezone.utc)))
    db.commit()

    assert _step(db, mine, "delivery").status == onboarding.IN_PROGRESS


def test_another_tenants_sync_does_not_complete_my_step(db):
    mine = _store(db, "mine3@example.test")
    theirs = _store(db, "theirs3@example.test")
    _connect(db, mine)
    channel = _connect(db, theirs, shop="other3.myshopify.com")
    _synced(db, theirs, channel)

    assert _step(db, mine, "sync").status == onboarding.NOT_STARTED


# --- support details, and never a stack trace -------------------------------

def test_support_details_come_from_the_environment(db, monkeypatch):
    monkeypatch.setenv("SUPPORT_EMAIL", "help@example.test")
    monkeypatch.setenv("SUPPORT_RESPONSE_TIME", "same business day")
    store = _store(db)
    result = onboarding.status(db, store=store)
    assert result.support_email == "help@example.test"
    assert result.support_response_time == "same business day"


def test_no_step_leaks_an_identifier_or_a_token(db):
    """A customer-facing checklist is not a debugging surface."""
    store = _store(db)
    _connect(db, store)
    rendered = json.dumps([s.to_dict() for s in onboarding.status(db, store=store).steps])
    assert str(store.id) not in rendered
    assert "shpat_" not in rendered
    assert "Traceback" not in rendered


# --- the links this screen offers have to go somewhere -----------------------

def test_every_help_url_has_a_page_behind_it():
    """`/help/failing-signature` was offered for weeks with no page behind it.

    The button appears when order notifications are being refused — when a
    store's orders have stopped arriving — so the one moment it is clicked is
    the worst possible moment to answer 404. Nothing checked, because the URL is
    written in Python and the route is a directory in the frontend, and neither
    half can see the other.

    This is that check. It reads the source rather than calling the function,
    because a help URL that only appears in a rare branch is exactly the one
    nobody exercises.
    """
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    source = (root / "backend" / "app" / "onboarding.py").read_text(encoding="utf-8")
    urls = set(re.findall(r'help_url\s*=\s*"([^"]+)"', source))
    assert urls, "no help_url found; has the attribute been renamed?"

    missing = []
    for url in urls:
        route = root / "frontend" / "app" / url.strip("/").replace("/", os.sep)
        if not any((route / f"page.{ext}").exists() for ext in ("tsx", "ts", "jsx", "js")):
            missing.append(url)
    assert missing == [], (
        f"these help links lead to a 404: {missing}. Add the page, or stop "
        f"offering the link.")
