"""The screen a seller uses to make a result possible.

"Add a cost" is not actionable on its own. What makes it actionable is knowing
which variant, how much it has sold, what is already on record and where that
figure came from — and, above all, which measurement is stuck without it.

The refusals matter as much as the writes. A cost entered in one currency
against sales in another must not be quietly converted, a value that says
nothing new must not fill the history, and nothing here may present a figure
somebody typed as one anybody checked.
"""
import datetime as dt
import os
import sys
import uuid
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import actions_engine, product_costs
from app.db import crud, models
from app.db.models import Base
from app.db.session import get_session
from app.main import app

PRODUCT = "gid://shopify/Product/1"
SMALL = "gid://shopify/ProductVariant/10"
LARGE = "gid://shopify/ProductVariant/20"


@pytest.fixture
def stack():
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


def _utc_today() -> dt.date:
    """The date the application works in.

    The machine's own date is a different day for part of every night, which is
    enough to make a cost recorded "today" invisible to a lookup asking for
    today — which is exactly how this test started failing.
    """
    return dt.datetime.now(dt.timezone.utc).date()


def _shop(factory, *, sold=((SMALL, "Small", 4, 400.0),), currency="USD"):
    db = factory()
    store = crud.get_or_create_dev_store(db)
    channel = models.ChannelConnection(
        store_id=store.id, provider="shopify",
        external_account_id="costs.myshopify.com", display_name="costs",
        status="connected", currency=currency,
        # A sync has read the last three months, so nothing here is waiting for
        # days: what is missing is a cost, which is what this screen is about.
        synced_at=dt.datetime.now(dt.timezone.utc),
        settings={"synced_from": (_utc_today() - dt.timedelta(days=90)).isoformat()})
    db.add(channel); db.flush()
    db.add(models.ChannelProduct(
        store_id=store.id, channel_id=channel.id, external_product_id=PRODUCT,
        title="The Complete Snowboard", status="ACTIVE", on_hand=30,
        is_gift_card=False, requires_shipping=True))
    for variant_id, title, units, revenue in sold:
        db.add(models.VariantDailyMetric(
            store_id=store.id, channel_id=channel.id, external_product_id=PRODUCT,
            external_variant_id=variant_id, variant_title=title,
            metric_date=_utc_today() - dt.timedelta(days=2),
            units=units, revenue=revenue, refunded_units=0, refunded_revenue=0,
            currency=currency, source="shopify_graphql"))
    db.commit(); db.close()
    return store


def _applied_action(factory, store):
    db = factory()
    db.add(models.OperatorAction(
        store_id=store.id, module="commerce", action_type="RESTOCK_PRODUCT",
        target="The Complete Snowboard",
        status=actions_engine.ActionStatus.APPLIED.value, evidence_mode="unverified",
        baseline={"on_hand": 1, "product_id": PRODUCT, "verified_on_hand": 30,
                  "days": 10, "revenue": 400.0},
        currency="USD", measurement_days=14, applied_by="human",
        applied_at=dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=20)))
    db.commit(); db.close()


# --- what the screen shows --------------------------------------------------

def test_a_variant_that_sold_is_listed_whether_or_not_it_has_a_cost(stack):
    client, factory = stack
    _shop(factory)
    body = client.get("/api/product-costs").json()
    assert body["without_cost"] == 1
    entry = body["entries"][0]
    assert entry["variant_title"] == "Small" and entry["units_sold"] == 4
    assert entry["unit_cost"] is None, "unknown, and never reported as zero"


def test_a_recorded_cost_shows_where_it_came_from(stack):
    client, factory = stack
    store = _shop(factory)
    db = factory()
    product_costs.record(db, store_id=store.id, product_id=PRODUCT, variant_id=SMALL,
                         amount=Decimal("20.00"), currency="USD",
                         effective_from=_utc_today() - dt.timedelta(days=30),
                         source=product_costs.SHOPIFY,
                         verification=product_costs.CONFIRMED, commit=True)
    db.close()

    entry = client.get("/api/product-costs").json()["entries"][0]
    assert entry["unit_cost"] == 20.0
    assert entry["source"] == "shopify" and entry["verification"] == "confirmed"


def test_recorded_cost_unlocks_product_margin_but_not_unknown_net_costs(stack):
    """The dashboard must use the cost catalogue without upgrading gross
    product margin into net profit when fees and advertising remain unknown.
    """
    client, factory = stack
    store = _shop(factory, sold=((SMALL, "Default Title", 1, 25.0),))
    db = factory()
    channel = db.scalar(select(models.ChannelConnection).where(
        models.ChannelConnection.store_id == store.id))
    sale_day = _utc_today() - dt.timedelta(days=2)
    db.add(models.CommerceDailyMetric(
        store_id=store.id, channel_id=channel.id, metric_date=sale_day,
        revenue=Decimal("25.00"), refunds=0, fees=0, landed_cogs=0,
        advertising_spend=0, orders=1, units=1, sessions=0,
        currency="USD", source="shopify_graphql", costs_complete=False))
    product_costs.record(
        db, store_id=store.id, product_id=PRODUCT, variant_id=SMALL,
        amount=Decimal("10.00"), currency="USD",
        effective_from=sale_day, source=product_costs.MANUAL,
        verification=product_costs.REPORTED)
    db.commit(); db.close()

    body = client.get("/api/dashboard/commerce?days=30").json()
    assert body["landed_cogs"] == 10.0
    assert body["gross_profit"] == 15.0
    assert body["gross_margin"] == 0.6
    assert body["cogs_complete"] is True
    assert body["profit"] is None, "fees and advertising are still unknown"


def test_the_screen_names_the_measurement_that_is_waiting(stack):
    """A cost with nothing behind it is housekeeping. A cost with a stuck result
    behind it is the difference between a number and a blank."""
    client, factory = stack
    store = _shop(factory)
    _applied_action(factory, store)
    body = client.get("/api/product-costs").json()
    assert body["entries"][0]["waiting_measurements"] == ["The Complete Snowboard"]
    assert body["blocking"] == 1


def test_a_cost_in_the_wrong_currency_is_flagged_rather_than_converted(stack):
    client, factory = stack
    store = _shop(factory, currency="USD")
    db = factory()
    product_costs.record(db, store_id=store.id, product_id=PRODUCT, variant_id=SMALL,
                         amount=Decimal("18.00"), currency="EUR",
                         effective_from=_utc_today(), source=product_costs.MANUAL,
                         verification=product_costs.REPORTED, commit=True)
    db.close()

    entry = client.get("/api/product-costs").json()["entries"][0]
    assert entry["currency_matches"] is False


def test_a_product_wide_cost_says_that_it_is_not_this_variants_own(stack):
    client, factory = stack
    store = _shop(factory)
    db = factory()
    product_costs.record(db, store_id=store.id, product_id=PRODUCT, variant_id="",
                         amount=Decimal("20.00"), currency="USD",
                         effective_from=_utc_today(), source=product_costs.MANUAL,
                         verification=product_costs.REPORTED, commit=True)
    db.close()

    entry = client.get("/api/product-costs").json()["entries"][0]
    assert entry["applies_to_every_variant"] is True


def test_a_sale_with_no_variant_says_it_cannot_be_attributed(stack):
    """Shopify gave us units but not which variant. No cost can honestly be
    attached to that, and pretending otherwise is a guess."""
    client, factory = stack
    _shop(factory, sold=((product_costs.unknown_variant_key(PRODUCT), None, 2, 200.0),))
    entry = client.get("/api/product-costs").json()["entries"][0]
    assert entry["attributable_to_variant"] is False
    assert entry["variant_id"] is None


def test_two_variants_are_listed_separately(stack):
    client, factory = stack
    _shop(factory, sold=((SMALL, "Small", 4, 400.0), (LARGE, "Large", 2, 400.0)))
    body = client.get("/api/product-costs").json()
    assert {entry["variant_title"] for entry in body["entries"]} == {"Small", "Large"}
    assert body["without_cost"] == 2


# --- writing one ------------------------------------------------------------

def test_a_seller_can_record_what_a_unit_costs_them(stack):
    client, factory = stack
    _shop(factory)
    body = client.post("/api/product-costs", json={
        "product_id": PRODUCT, "variant_id": SMALL, "variant_title": "Small",
        "purchase_amount": 18.5, "extra_amount": 1.5, "currency": "USD"}).json()
    assert body["amount"] == 20.0
    assert body["purchase_amount"] == 18.5 and body["extra_amount"] == 1.5


def test_what_a_seller_types_is_reported_and_never_confirmed(stack):
    """Nobody checked it. It still counts — they know what they paid — but the
    claim that follows is theirs as much as ours, and the screens say so."""
    client, factory = stack
    _shop(factory)
    body = client.post("/api/product-costs", json={
        "product_id": PRODUCT, "variant_id": SMALL,
        "purchase_amount": 20, "currency": "USD"}).json()
    assert body["source"] == "manual" and body["verification"] == "reported"


def test_a_cost_can_be_dated_to_when_it_became_true(stack):
    client, factory = stack
    _shop(factory)
    body = client.post("/api/product-costs", json={
        "product_id": PRODUCT, "variant_id": SMALL, "purchase_amount": 20,
        "currency": "USD", "effective_from": "2026-03-01"}).json()
    assert body["effective_from"] == "2026-03-01"


def test_a_negative_cost_is_refused(stack):
    client, factory = stack
    _shop(factory)
    response = client.post("/api/product-costs", json={
        "product_id": PRODUCT, "purchase_amount": -1, "currency": "USD"})
    assert response.status_code == 422


def test_the_same_figure_again_is_refused_rather_than_stacked(stack):
    client, factory = stack
    _shop(factory)
    payload = {"product_id": PRODUCT, "variant_id": SMALL,
               "purchase_amount": 20, "currency": "USD"}
    assert client.post("/api/product-costs", json=payload).status_code == 200
    assert client.post("/api/product-costs", json=payload).status_code == 422


def test_recording_a_cost_is_audited(stack):
    client, factory = stack
    _shop(factory)
    client.post("/api/product-costs", json={
        "product_id": PRODUCT, "variant_id": SMALL,
        "purchase_amount": 20, "currency": "USD"})
    db = factory()
    events = [row.action for row in db.scalars(select(models.AuditEvent))]
    db.close()
    assert "commerce.cost_recorded" in events


# --- the history ------------------------------------------------------------

def test_the_history_keeps_every_value_ever_recorded(stack):
    """Nothing is overwritten, so this is the audit of a number that decides
    what the payroll may claim."""
    client, factory = stack
    _shop(factory)
    client.post("/api/product-costs", json={
        "product_id": PRODUCT, "variant_id": SMALL, "purchase_amount": 20,
        "currency": "USD", "effective_from": "2026-01-01"})
    client.post("/api/product-costs", json={
        "product_id": PRODUCT, "variant_id": SMALL, "purchase_amount": 26,
        "currency": "USD", "effective_from": "2026-06-01"})

    costs = client.get("/api/product-costs/history").json()["costs"]
    assert [entry["amount"] for entry in costs] == [26.0, 20.0]
    assert costs[0]["effective_from"] == "2026-06-01"


def test_the_export_carries_the_costs_the_seller_entered(stack):
    """The one thing they cannot get back out of Shopify is the one thing we
    keep."""
    client, factory = stack
    _shop(factory)
    client.post("/api/product-costs", json={
        "product_id": PRODUCT, "variant_id": SMALL,
        "purchase_amount": 20, "currency": "USD", "note": "supplier A"})
    export = client.get("/api/privacy/export").json()
    assert len(export["product_costs"]) == 1
    assert export["product_costs"][0]["note"] == "supplier A"
