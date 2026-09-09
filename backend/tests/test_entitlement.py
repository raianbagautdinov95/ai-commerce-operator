"""
Tests for who may do the paid work.

Before this existed nothing asked. A trial that ended and a payment that failed
both left the customer with everything — syncs, analyses, the lot — because the
subscription row recorded the truth and no code read it. That is the shape of a
product that is free by accident.

The two failures worth guarding are opposite. Letting an expired customer keep
working means never being paid; blocking the wrong things means holding
somebody's data hostage over a card, which is worse. So half of these check what
stays open.
"""
import base64
import datetime as dt
import json
import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import billing, entitlement
from app.db import crud, models
from app.db.models import Base
from app.db.session import get_session
from app.main import app


@pytest.fixture(autouse=True)
def _stripe(monkeypatch):
    """Configured, but pointed at nothing real. No test here reaches Stripe."""
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_not_a_real_key")
    monkeypatch.setenv("STRIPE_PRICE_OPERATOR", "price_not_real")
    monkeypatch.setenv("QUEUE_ENABLED", "false")


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


def _with_status(db, store, status, *, trial_days=7, period_end=None):
    row = billing.ensure_subscription(db, store_id=store.id)
    row.status = status
    row.trial_ends_at = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=trial_days)
    row.current_period_end = period_end
    db.add(row); db.commit()
    return row


# --- the six states ---------------------------------------------------------

@pytest.mark.parametrize("stored,expected,access", [
    ("trialing", entitlement.TRIALING, True),
    ("active", entitlement.ACTIVE, True),
    ("past_due", entitlement.PAST_DUE, False),
    ("unpaid", entitlement.PAST_DUE, False),
    ("canceled", entitlement.CANCELED, False),
    ("trial_expired", entitlement.EXPIRED, False),
])
def test_each_stored_status_maps_to_one_contract_state(db, stored, expected, access):
    store = _store(db)
    _with_status(db, store, stored)
    state = entitlement.current(db, store=store)
    assert state.status == expected
    assert state.access is access


def test_a_deployment_without_stripe_limits_nothing(db, monkeypatch):
    """A self-hosted deployment must not lock its owner out of their own data
    because nobody set a price. Refusing there invents a paywall this
    deployment never claimed to have."""
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    store = _store(db)
    _with_status(db, store, "trial_expired")
    state = entitlement.current(db, store=store)
    assert state.status == entitlement.NOT_CONFIGURED
    assert state.access is True


def test_the_trial_reports_days_and_not_a_guessed_date(db):
    store = _store(db)
    _with_status(db, store, "trialing", trial_days=5)
    state = entitlement.current(db, store=store)
    assert state.trial_days_remaining == 5
    assert state.period_ends_at is not None


def test_a_paid_period_end_is_reported_only_when_stripe_gave_one(db):
    """A renewal date invented here is a date a customer plans around."""
    store = _store(db)
    _with_status(db, store, "active", period_end=None)
    assert entitlement.current(db, store=store).period_ends_at is None

    end = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=30)
    _with_status(db, store, "active", period_end=end)
    assert entitlement.current(db, store=store).period_ends_at is not None


def test_each_state_offers_the_right_next_step(db):
    store = _store(db)
    for stored, action in (("trialing", "checkout"), ("trial_expired", "checkout"),
                           ("canceled", "checkout"), ("past_due", "portal"),
                           ("active", "portal")):
        _with_status(db, store, stored)
        assert entitlement.current(db, store=store).action == action


def test_checkout_is_unavailable_without_a_price(db, monkeypatch):
    """Offering a button that 503s is worse than saying why it is not there."""
    monkeypatch.delenv("STRIPE_PRICE_OPERATOR", raising=False)
    store = _store(db)
    _with_status(db, store, "trialing")
    assert entitlement.current(db, store=store).checkout_available is False


def test_the_contract_carries_no_identifier(db):
    """Not the Stripe customer, not the subscription, not the store."""
    store = _store(db)
    row = _with_status(db, store, "active")
    row.stripe_customer_id = "cus_secret"
    row.stripe_subscription_id = "sub_secret"
    db.add(row); db.commit()

    rendered = json.dumps(entitlement.current(db, store=store).to_dict(), default=str)
    assert "cus_secret" not in rendered
    assert "sub_secret" not in rendered
    assert str(store.id) not in rendered


# --- enforcement, in the endpoint rather than the browser -------------------

def test_an_expired_customer_cannot_run_the_paid_work(db):
    store = _store(db)
    _with_status(db, store, "trial_expired")
    with pytest.raises(entitlement.PaymentRequired):
        entitlement.require_access(db, store=store)


def test_past_due_blocks_it_too(db):
    store = _store(db)
    _with_status(db, store, "past_due")
    with pytest.raises(entitlement.PaymentRequired):
        entitlement.require_access(db, store=store)


def test_a_trial_and_a_paying_customer_are_not_blocked(db):
    store = _store(db)
    for stored in ("trialing", "active"):
        _with_status(db, store, stored)
        assert entitlement.require_access(db, store=store).access is True


def _client(db):
    def override():
        yield db
    app.dependency_overrides[get_session] = override
    return TestClient(app)


def test_the_api_refuses_the_scan_with_402_and_an_explanation(db):
    """The endpoint, not the screen. Anybody can call the API directly, and a
    limit that lives in a browser is not a limit."""
    store = crud.get_or_create_dev_store(db)
    _with_status(db, store, "trial_expired")
    try:
        response = _client(db).post("/api/commerce/scan")
    finally:
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 402
    body = response.json()
    assert body["status"] == entitlement.EXPIRED
    assert body["action"] == "checkout"
    assert "Nothing has been deleted" in body["detail"]


def test_what_stays_open_when_access_has_lapsed(db):
    """Taking somebody's data hostage over a failed card is not a business
    model, and locking the billing page behind billing is a loop."""
    store = crud.get_or_create_dev_store(db)
    _with_status(db, store, "trial_expired")
    client = _client(db)
    try:
        assert client.get("/api/billing").status_code == 200
        assert client.get("/api/privacy/export").status_code == 200
        assert client.get("/api/onboarding").status_code == 200
        assert client.get("/api/actions").status_code == 200
        assert client.post("/api/privacy/deletion-request",
                           json={"confirmation": "DELETE MY WORKSPACE"}).status_code == 200
    finally:
        app.dependency_overrides.pop(get_session, None)


def test_cancelling_deletes_nothing(db):
    """Their data outlives the subscription. It has to: they may come back, and
    they are entitled to export it either way."""
    store = crud.get_or_create_dev_store(db)
    db.add(models.OperatorAction(
        store_id=store.id, module="commerce", action_type="RESTOCK_PRODUCT",
        target="A Snowboard", status="proposed", evidence_mode="unverified",
        currency="USD", measurement_days=14))
    db.commit()
    _with_status(db, store, "canceled")

    assert entitlement.current(db, store=store).access is False
    assert list(db.scalars(select(models.OperatorAction)))


# --- the plan itself --------------------------------------------------------

def test_one_plan_is_sold_and_its_price_comes_from_configuration(db, monkeypatch):
    monkeypatch.setenv("PLAN_PRICE_OPERATOR", "39")
    monkeypatch.setenv("PLAN_CURRENCY", "eur")
    store = _store(db)
    _with_status(db, store, "trialing")
    state = entitlement.current(db, store=store)
    assert state.price_per_month == 39.0
    assert state.currency == "EUR"


def test_the_features_listed_are_things_that_exist():
    """No roadmap, no coming soon, and nothing about guaranteed returns."""
    joined = " ".join(billing.PILOT_FEATURES).lower()
    for forbidden in ("guarantee", "roi", "coming soon", "will earn", "profit you"):
        assert forbidden not in joined
    assert any("read-only" in f.lower() for f in billing.PILOT_FEATURES)
    assert any("approv" in f.lower() for f in billing.PILOT_FEATURES)


# --- one customer's billing is not another's --------------------------------

def test_one_tenant_never_sees_another_subscription(db):
    mine = _store(db, "mine@example.test")
    theirs = _store(db, "theirs@example.test")
    _with_status(db, mine, "trialing")
    row = _with_status(db, theirs, "active")
    row.stripe_customer_id = "cus_theirs"
    db.add(row); db.commit()

    state = entitlement.current(db, store=mine)
    assert state.status == entitlement.TRIALING
    assert "cus_theirs" not in json.dumps(state.to_dict(), default=str)
