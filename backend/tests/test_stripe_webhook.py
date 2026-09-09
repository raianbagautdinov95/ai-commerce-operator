"""
Tests for the Stripe webhook.

This endpoint is the source of truth for whether a customer has paid, and it has
the two properties that make a bug here expensive: it is public, so nothing
carries a tenant into the transaction, and Stripe retries, so answering wrongly
once decides the matter for ever.

Both failed. The subscription lookup ran with no tenant declared against a table
under FORCE row-level security, so every event found nothing and returned 404 —
billing frozen at whatever it last was while Stripe believed it had been told.
And the receipt was committed before the work, so a process dying in between
left a row saying `processing` and the retry was answered "duplicate" with a
200, dropping the event.
"""
import base64
import hashlib
import hmac
import json
import os
import sys
import time
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import billing
from app.db import crud, models
from app.db.models import Base
from app.db.session import get_session
from app.main import app

SECRET = "whsec_test_secret"


def _configure(monkeypatch):
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_not_a_real_key")


def _signed(body: bytes) -> str:
    stamp = int(time.time())
    digest = hmac.new(SECRET.encode(), f"{stamp}.".encode() + body,
                      hashlib.sha256).hexdigest()
    return f"t={stamp},v1={digest}"


def _store():
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    db = factory()
    store = crud.get_or_create_dev_store(db)
    billing.ensure_subscription(db, store_id=store.id)
    db.commit()
    store_id = str(store.id)
    db.close()
    return factory, store_id


_UNSET = object()


def _post(factory, payload: dict, *, signature=_UNSET):
    """`signature=None` means send none; omitting it means send a valid one.

    Not a default of "", because an empty string is falsy and an earlier version
    of this helper quietly signed the request it was meant to leave unsigned —
    the test passed against an endpoint that had never been asked the question.
    """
    body = json.dumps(payload).encode()

    def override():
        session = factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override
    try:
        headers = {"Content-Type": "application/json"}
        if signature is _UNSET:
            headers["Stripe-Signature"] = _signed(body)
        elif signature is not None:
            headers["Stripe-Signature"] = signature
        return TestClient(app).post("/api/webhooks/stripe", content=body,
                                    headers=headers)
    finally:
        app.dependency_overrides.pop(get_session, None)


def _completed(store_id: str, event_id: str = "evt_1") -> dict:
    return {"id": event_id, "type": "checkout.session.completed",
            "data": {"object": {"id": "cs_1", "customer": "cus_1",
                                "subscription": "sub_1",
                                "client_reference_id": store_id,
                                "metadata": {"store_id": store_id,
                                             "plan": "operator"}}}}


# --- the signature is the only thing that authorises this ------------------

def test_an_unsigned_event_is_refused(monkeypatch):
    _configure(monkeypatch)
    factory, store_id = _store()
    assert _post(factory, _completed(store_id), signature=None).status_code == 401


def test_a_forged_signature_is_refused(monkeypatch):
    _configure(monkeypatch)
    factory, store_id = _store()
    response = _post(factory, _completed(store_id),
                     signature="t=1,v1=deadbeef")
    assert response.status_code == 401


# --- a paid customer becomes active ----------------------------------------

def test_a_completed_checkout_activates_the_subscription(monkeypatch):
    _configure(monkeypatch)
    factory, store_id = _store()
    assert _post(factory, _completed(store_id)).json() == {"status": "accepted"}

    db = factory()
    row = db.scalar(select(models.Subscription))
    assert row.status == "active"
    assert row.stripe_customer_id == "cus_1"
    assert row.stripe_subscription_id == "sub_1"
    db.close()


@pytest.mark.parametrize("stripe_status,expected", [
    ("active", "active"),
    ("trialing", "trialing"),
    ("past_due", "past_due"),
    ("unpaid", "past_due"),
    ("canceled", "canceled"),
])
def test_stripe_status_decides_ours(monkeypatch, stripe_status, expected):
    """Stripe is the source of truth; we do not infer payment from anything
    else, and never compute it."""
    _configure(monkeypatch)
    factory, store_id = _store()
    _post(factory, {
        "id": f"evt_{stripe_status}", "type": "customer.subscription.updated",
        "data": {"object": {"id": "sub_1", "status": stripe_status,
                            "metadata": {"store_id": store_id}}}})
    db = factory()
    assert db.scalar(select(models.Subscription)).status == expected
    db.close()


# --- retries must not create a second anything -----------------------------

def test_the_same_event_twice_changes_nothing_the_second_time(monkeypatch):
    """Stripe redelivers. Two subscriptions from one payment would be a refund
    conversation."""
    _configure(monkeypatch)
    factory, store_id = _store()
    first = _post(factory, _completed(store_id))
    second = _post(factory, _completed(store_id))

    assert first.json() == {"status": "accepted"}
    assert second.json() == {"status": "duplicate"}

    db = factory()
    assert db.scalar(select(models.Subscription).where(
        models.Subscription.store_id == uuid.UUID(store_id))) is not None
    subscriptions = list(db.scalars(select(models.Subscription)))
    deliveries = list(db.scalars(select(models.WebhookDelivery)))
    assert len(subscriptions) == 1, "a redelivery created a second subscription"
    assert len(deliveries) == 1, "a redelivery created a second receipt"
    db.close()


def test_a_retry_of_an_unfinished_delivery_is_not_called_a_duplicate(monkeypatch):
    """The receipt used to be committed before the work. A process dying in
    between left `processing` for ever, and the retry was answered 200 —
    dropping an event that says whether somebody paid."""
    _configure(monkeypatch)
    factory, store_id = _store()

    db = factory()
    row = db.scalar(select(models.Subscription))
    db.add(models.WebhookDelivery(
        store_id=row.store_id, provider="stripe", delivery_id="evt_stuck",
        topic="checkout.session.completed", payload_hash="x", status="processing"))
    db.commit(); db.close()

    response = _post(factory, _completed(store_id, event_id="evt_stuck"))
    assert response.status_code == 409, "a 200 here would end Stripe's retries"


def test_an_event_for_an_unknown_store_is_refused(monkeypatch):
    _configure(monkeypatch)
    factory, _ = _store()
    response = _post(factory, _completed(str(uuid.uuid4()), event_id="evt_x"))
    assert response.status_code == 404


# --- the tenant is declared, which RLS requires ----------------------------

def test_the_handler_declares_the_tenant_before_it_reads(monkeypatch):
    """SQLite cannot show this: subscriptions is under FORCE row-level security
    on PostgreSQL, and this endpoint is public, so without an explicit
    declaration the lookup matches nothing and every event 404s.

    Checked structurally because the failure only exists on a database this
    part of the suite does not run against.
    """
    import inspect

    from app.routers import account

    source = inspect.getsource(account.stripe_webhook)
    before_lookup = source.split("select(models.Subscription)")[0]
    assert "declare_tenant" in before_lookup, \
        "the subscription is read before any tenant is declared"


def _epoch(value) -> int:
    """The stored instant as a UTC epoch.

    SQLite hands datetimes back without a timezone, and `.timestamp()` on a
    naive value reads it as local time — so this assertion would pass or fail
    depending on where the machine is.
    """
    import datetime as _dt

    if value.tzinfo is None:
        value = value.replace(tzinfo=_dt.timezone.utc)
    return int(value.timestamp())


# --- the shape Stripe sends is not the shape we assumed ----------------------
#
# Both of these were invisible for the same reason: the payloads in this file
# were written by the same hand as the reader, so they contained exactly the
# fields the reader looked for. A field Stripe stopped sending and a field
# Stripe does send but we ignored were equally unrepresented.

def test_a_checkout_that_completed_unpaid_does_not_hand_over_the_product(monkeypatch):
    """A subscription Checkout completes even when the money has not arrived —
    a card that needed authentication, or one that failed after submission. The
    subscription is then `incomplete`, and Stripe sends an update if it ever
    settles. Activating on the session alone gives the product away."""
    _configure(monkeypatch)
    factory, store_id = _store()
    event = _completed(store_id)
    event["data"]["object"]["payment_status"] = "unpaid"

    assert _post(factory, event).json() == {"status": "accepted"}

    db = factory()
    row = db.scalar(select(models.Subscription))
    assert row.status != "active", "an unpaid checkout was granted access"
    # Still recorded: this is how the Portal is opened and how the later
    # subscription event finds this row.
    assert row.stripe_customer_id == "cus_1"
    assert row.stripe_subscription_id == "sub_1"
    db.close()


@pytest.mark.parametrize("payment_status", ["paid", "no_payment_required"])
def test_a_checkout_that_took_the_money_still_activates(monkeypatch, payment_status):
    """The other half: a fix that refuses real customers is worse than the bug.
    `no_payment_required` is what a trial reports."""
    _configure(monkeypatch)
    factory, store_id = _store()
    event = _completed(store_id)
    event["data"]["object"]["payment_status"] = payment_status

    _post(factory, event)
    db = factory()
    assert db.scalar(select(models.Subscription)).status == "active"
    db.close()


def test_a_period_end_on_the_subscription_item_is_read(monkeypatch):
    """Stripe reports the period on the subscription in some API versions and on
    its items in others. Reading one place meant the date a customer is shown
    stopped moving the day their account was upgraded — silently, because a
    missing key raises nothing."""
    _configure(monkeypatch)
    factory, store_id = _store()
    _post(factory, {
        "id": "evt_item_period", "type": "customer.subscription.updated",
        "data": {"object": {"id": "sub_1", "status": "active",
                            "metadata": {"store_id": store_id},
                            "items": {"data": [{"current_period_end": 1790000000}]}}}})

    db = factory()
    row = db.scalar(select(models.Subscription))
    assert row.current_period_end is not None, "the period end was never read"
    assert _epoch(row.current_period_end) == 1790000000
    db.close()


def test_the_furthest_item_decides_when_access_ends(monkeypatch):
    """A subscription with several items ends when its last one does. Taking the
    earliest would cut somebody's access short while they are still paying."""
    _configure(monkeypatch)
    factory, store_id = _store()
    _post(factory, {
        "id": "evt_two_items", "type": "customer.subscription.updated",
        "data": {"object": {"id": "sub_1", "status": "active",
                            "metadata": {"store_id": store_id},
                            "items": {"data": [{"current_period_end": 1780000000},
                                               {"current_period_end": 1790000000}]}}}})
    db = factory()
    assert _epoch(db.scalar(select(models.Subscription)).current_period_end) \
        == 1790000000
    db.close()


def test_a_payload_carrying_no_period_end_leaves_the_old_one_alone(monkeypatch):
    """Not told is not expired."""
    _configure(monkeypatch)
    factory, store_id = _store()
    _post(factory, {
        "id": "evt_has_period", "type": "customer.subscription.updated",
        "data": {"object": {"id": "sub_1", "status": "active",
                            "metadata": {"store_id": store_id},
                            "current_period_end": 1790000000}}})
    _post(factory, {
        "id": "evt_no_period", "type": "customer.subscription.updated",
        "data": {"object": {"id": "sub_1", "status": "active",
                            "metadata": {"store_id": store_id}}}})

    db = factory()
    assert _epoch(db.scalar(select(models.Subscription)).current_period_end) \
        == 1790000000
    db.close()


def test_the_api_version_is_sent_when_this_deployment_pins_one(monkeypatch):
    """Unpinned, every request is answered in whatever version the account is
    currently on, and Stripe moves that on its own schedule."""
    import httpx

    from app import stripe_billing

    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_x")
    monkeypatch.setenv("STRIPE_PRICE_OPERATOR", "price_1")
    monkeypatch.setenv("STRIPE_SUCCESS_URL", "https://app.example.test/ok")
    monkeypatch.setenv("STRIPE_CANCEL_URL", "https://app.example.test/no")
    monkeypatch.setenv("STRIPE_API_VERSION", "2026-01-01.test")

    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["version"] = request.headers.get("Stripe-Version")
        return httpx.Response(200, json={"url": "https://checkout.example.test/s"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    stripe_billing.create_checkout(store_id="s", email="a@b.test", plan="operator",
                                   client=client)
    assert seen["version"] == "2026-01-01.test"


def test_nothing_is_pinned_when_nothing_is_configured(monkeypatch):
    """An unpinned deployment still works — `period_end` reads both shapes — so
    this must not start sending an empty header."""
    import httpx

    from app import stripe_billing

    monkeypatch.delenv("STRIPE_API_VERSION", raising=False)
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_x")
    monkeypatch.setenv("STRIPE_PRICE_OPERATOR", "price_1")
    monkeypatch.setenv("STRIPE_SUCCESS_URL", "https://app.example.test/ok")
    monkeypatch.setenv("STRIPE_CANCEL_URL", "https://app.example.test/no")

    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["has_version"] = "Stripe-Version" in request.headers
        return httpx.Response(200, json={"url": "https://checkout.example.test/s"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    stripe_billing.create_checkout(store_id="s", email="a@b.test", plan="operator",
                                   client=client)
    assert seen["has_version"] is False
