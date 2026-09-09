"""
Tests for confirming and pricing a restock.

This is where the product's central claim is either kept or quietly broken. The
seller says they restocked; the store is asked whether that is true; and only an
answer that came back from Shopify — plus a window that has actually elapsed —
may ever be called proven. Most of what follows checks the refusals.
"""
import datetime as dt
import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import actions_engine, credentials, product_costs, shopify
from app.db import crud, models
from app.db.models import Base
from app.db.session import get_session
from app.main import app

PRODUCT = "gid://shopify/Product/1"
VARIANT = "gid://shopify/ProductVariant/10"


class _FakeClient:
    """Stands in for Shopify. `reads` is what the shelf says when asked."""

    def __init__(self, reads):
        self._reads = reads

    def product_inventory(self, product_id):
        return self._reads

    def close(self):
        pass


@pytest.fixture
def store(monkeypatch):
    """A tenant with a connected shop and one proposed restock."""
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    db = factory()
    tenant = crud.get_or_create_dev_store(db)
    channel = models.ChannelConnection(
        store_id=tenant.id, provider="shopify",
        external_account_id="restock-shop.myshopify.com", display_name="restock",
        status="connected", currency="USD", settings={},
    )
    db.add(channel); db.flush()
    action = models.OperatorAction(
        store_id=tenant.id, module="commerce", action_type="RESTOCK_PRODUCT",
        target="Snowboard", status=actions_engine.ActionStatus.PROPOSED.value,
        evidence_mode="unverified", source_type="commerce_daily_metrics",
        source_id=PRODUCT, projected_impact=None,
        baseline={"days": 10, "revenue": 400.0, "spend": 0.0, "on_hand": 4,
                  "product_id": PRODUCT},
        revert_to={"revertible": False}, note="runs out soon",
        currency="USD", measurement_days=14,
    )
    db.add(action); db.commit()
    action_id = str(action.id)
    db.close()

    monkeypatch.setattr(credentials, "load_credential", lambda *a, **k: "token")
    return factory, action_id


def _client(factory):
    def override():
        session = factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override
    return TestClient(app)


def _call(factory, path, reads=None, monkeypatch=None):
    if monkeypatch is not None:
        monkeypatch.setattr(shopify, "AdminGraphQLClient",
                            lambda *a, **k: _FakeClient(reads))
    try:
        return _client(factory).post(path)
    finally:
        app.dependency_overrides.pop(get_session, None)


def _action(factory, action_id):
    db = factory()
    try:
        return db.get(models.OperatorAction, __import__("uuid").UUID(action_id))
    finally:
        db.close()


# --- confirmation is the store's answer, not the seller's -------------------

def test_a_restock_nobody_can_see_is_refused(store, monkeypatch):
    """The seller says they did it and Shopify says the shelf is unchanged. The
    seller does not win that argument."""
    factory, action_id = store
    body = _call(factory, f"/api/commerce/actions/{action_id}/confirm",
                 reads=4, monkeypatch=monkeypatch).json()
    assert body["confirmed"] is False
    assert body["on_hand_now"] == 4

    row = _action(factory, action_id)
    assert row.status == "proposed", "nothing may be recorded on an unwitnessed claim"
    assert "verified_on_hand" not in row.baseline


def test_untracked_stock_is_not_a_confirmation(store, monkeypatch):
    """Shopify returning nothing is not Shopify returning a bigger number."""
    factory, action_id = store
    body = _call(factory, f"/api/commerce/actions/{action_id}/confirm",
                 reads=None, monkeypatch=monkeypatch).json()
    assert body["confirmed"] is False
    assert _action(factory, action_id).status == "proposed"


def test_a_shelf_that_grew_is_recorded_with_what_was_seen(store, monkeypatch):
    factory, action_id = store
    body = _call(factory, f"/api/commerce/actions/{action_id}/confirm",
                 reads=30, monkeypatch=monkeypatch).json()
    assert body["confirmed"] is True

    row = _action(factory, action_id)
    assert row.status == "applied"
    assert row.applied_by == "human"
    assert row.baseline["verified_on_hand"] == 30
    assert row.baseline["on_hand"] == 4, "the before picture must survive intact"


def test_an_action_the_api_cannot_witness_is_not_confirmable(store, monkeypatch):
    """Entering landed costs happens in a spreadsheet nobody can read back."""
    factory, action_id = store
    db = factory()
    row = db.get(models.OperatorAction, __import__("uuid").UUID(action_id))
    row.action_type = "ENTER_LANDED_COSTS"
    db.add(row); db.commit(); db.close()

    response = _call(factory, f"/api/commerce/actions/{action_id}/confirm",
                     reads=30, monkeypatch=monkeypatch)
    assert response.status_code == 409


# --- measurement ------------------------------------------------------------

def _confirm_and_backdate(factory, action_id, monkeypatch, *, days_ago: int,
                          units_per_day: int = 1, revenue_per_day: float = 60.0,
                          cover: bool = True, unit_cost: str | None = "20.00"):
    """Witness the restock, then pretend it happened `days_ago` with sales since.

    `cover` records what a sync would have recorded: which days were read. A
    window nothing has read is not a window of zero sales, and the tests that
    turn this off are checking exactly that.
    """
    _call(factory, f"/api/commerce/actions/{action_id}/confirm",
          reads=30, monkeypatch=monkeypatch)
    db = factory()
    row = db.get(models.OperatorAction, __import__("uuid").UUID(action_id))
    applied = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days_ago)
    row.applied_at = applied
    db.add(row)
    channel = db.scalar(select(models.ChannelConnection))
    for offset in range(1, days_ago):
        day = (applied + dt.timedelta(days=offset)).date()
        db.add(models.ProductDailyMetric(
            store_id=row.store_id, channel_id=channel.id, external_product_id=PRODUCT,
            metric_date=day, units=units_per_day,
            revenue=Decimal(str(revenue_per_day)), currency="USD",
            source="shopify_graphql",
        ))
        db.add(models.VariantDailyMetric(
            store_id=row.store_id, channel_id=channel.id, external_product_id=PRODUCT,
            external_variant_id=VARIANT, variant_title="Standard", metric_date=day,
            units=units_per_day, revenue=Decimal(str(revenue_per_day)),
            refunded_units=0, refunded_revenue=0, currency="USD",
            source="shopify_graphql",
        ))
    if unit_cost is not None:
        product_costs.record(
            db, store_id=row.store_id, product_id=PRODUCT, variant_id=VARIANT,
            amount=Decimal(unit_cost), currency="USD",
            effective_from=applied.date(), source=product_costs.SHOPIFY,
            verification=product_costs.CONFIRMED)
    if cover:
        channel.synced_at = dt.datetime.now(dt.timezone.utc)
        channel.settings = {**(channel.settings or {}),
                            "synced_from": (applied - dt.timedelta(days=1)).date().isoformat()}
        db.add(channel)
    db.commit(); db.close()


def test_an_open_window_is_not_measured_at_all(store, monkeypatch):
    """A provisional reading used to be available here, and it was a trap: it
    recorded a result, which stopped the window from ever being priced in full."""
    factory, action_id = store
    _confirm_and_backdate(factory, action_id, monkeypatch, days_ago=6)
    response = _client(factory).post(f"/api/commerce/actions/{action_id}/measure")
    app.dependency_overrides.pop(get_session, None)
    assert response.status_code == 422
    assert "full day(s) to go" in response.json()["detail"]

    db = factory()
    row = db.get(models.OperatorAction, __import__("uuid").UUID(action_id))
    assert row.measured_at is None and row.impact is None
    db.close()


def test_a_closed_window_is_priced_by_what_the_old_stock_could_not_have_covered(
        store, monkeypatch):
    """Fourteen full days at one unit a day, against four already on the shelf:
    ten of those sales needed the restock, at 60.00 each and 20.00 of cost."""
    factory, action_id = store
    _confirm_and_backdate(factory, action_id, monkeypatch, days_ago=16)
    body = _client(factory).post(f"/api/commerce/actions/{action_id}/measure").json()
    app.dependency_overrides.pop(get_session, None)

    assert body["status"] == "measured"
    assert body["outcome"]["days"] == 14, "the day of the change belongs to neither side"
    assert body["impact"]["observed_units"] == 14
    assert body["impact"]["attributable_units"] == 10
    assert body["impact"]["attributable_revenue"] == 600.0
    assert body["impact"]["attributable_cost"] == 200.0
    assert body["impact"]["net"] == 400.0
    assert body["impact"]["provisional"] is False


def test_a_margin_with_every_condition_met_reaches_the_headline(store, monkeypatch):
    """Witnessed, closed, covered, incremental, costed and in one currency."""
    factory, action_id = store
    _confirm_and_backdate(factory, action_id, monkeypatch, days_ago=16)
    client = _client(factory)
    body = client.post(f"/api/commerce/actions/{action_id}/measure").json()
    payroll = client.get("/api/dashboard/payroll?days=30&explain=false").json()
    app.dependency_overrides.pop(get_session, None)

    assert body["evidence_mode"] == "real"
    assert payroll["settled_impact"] == 400.0
    assert payroll["excluded_unverified"] == 0


def test_a_window_with_no_cost_is_refused_rather_than_priced_at_revenue(
        store, monkeypatch):
    """The sales are known and the margin is not. Calling the revenue a result
    would be the whole sale price presented as earnings."""
    factory, action_id = store
    _confirm_and_backdate(factory, action_id, monkeypatch, days_ago=16, unit_cost=None)
    response = _client(factory).post(f"/api/commerce/actions/{action_id}/measure")
    app.dependency_overrides.pop(get_session, None)
    assert response.status_code == 422
    assert "no unit cost" in response.json()["detail"]


def test_sales_the_old_stock_could_have_covered_prove_nothing(store, monkeypatch):
    """Four were already there and four sold. The restock has not yet been the
    reason for a single sale, and no amount of elapsed time changes that."""
    factory, action_id = store
    _confirm_and_backdate(factory, action_id, monkeypatch, days_ago=16,
                          units_per_day=0)
    db = factory()
    row = db.get(models.OperatorAction, __import__("uuid").UUID(action_id))
    for offset in range(1, 5):
        day = (row.applied_at + dt.timedelta(days=offset)).date()
        for table in (models.ProductDailyMetric, models.VariantDailyMetric):
            metric = db.scalar(select(table).where(table.metric_date == day))
            metric.units = 1
            db.add(metric)
    db.commit(); db.close()

    body = _client(factory).post(f"/api/commerce/actions/{action_id}/measure").json()
    app.dependency_overrides.pop(get_session, None)
    assert body["impact"]["observed_units"] == 4
    assert body["impact"]["attributable_units"] == 0
    assert body["impact"]["net"] == 0.0
    assert body["evidence_mode"] == "unverified"
    assert "already on" in body["impact"]["evidence_reason"]


def test_a_measured_but_unwitnessed_action_never_becomes_real(store, monkeypatch):
    """Measuring carefully is not the same as having watched it happen."""
    factory, action_id = store
    _confirm_and_backdate(factory, action_id, monkeypatch, days_ago=16)
    db = factory()
    row = db.get(models.OperatorAction, __import__("uuid").UUID(action_id))
    row.baseline = {k: v for k, v in row.baseline.items() if k != "verified_on_hand"}
    db.add(row); db.commit(); db.close()

    body = _client(factory).post(f"/api/commerce/actions/{action_id}/measure").json()
    app.dependency_overrides.pop(get_session, None)
    assert body["status"] == "measured"
    assert body["evidence_mode"] == "unverified"
    assert "read back from Shopify" in body["impact"]["evidence_reason"]


def test_a_window_nobody_read_is_not_a_window_of_no_sales(store, monkeypatch):
    """The days elapsed while nothing was syncing. Pricing that as a failed
    restock would turn a broken integration into a bad result."""
    factory, action_id = store
    _confirm_and_backdate(factory, action_id, monkeypatch, days_ago=16, cover=False)
    db = factory()
    channel = db.scalar(select(models.ChannelConnection))
    channel.synced_at = None
    channel.settings = {}
    db.add(channel); db.commit(); db.close()

    response = _client(factory).post(f"/api/commerce/actions/{action_id}/measure")
    app.dependency_overrides.pop(get_session, None)
    assert response.status_code == 422
    assert "never completed a sync" in response.json()["detail"]

    db = factory()
    row = db.get(models.OperatorAction, __import__("uuid").UUID(action_id))
    assert row.measured_at is None, "waiting, not measured as zero"
    db.close()


def test_a_sync_that_did_not_reach_back_far_enough_is_refused(store, monkeypatch):
    """A seven-day sync cannot certify a fortnight."""
    factory, action_id = store
    _confirm_and_backdate(factory, action_id, monkeypatch, days_ago=16)
    db = factory()
    channel = db.scalar(select(models.ChannelConnection))
    channel.settings = {**channel.settings,
                        "synced_from": (dt.date.today() - dt.timedelta(days=7)).isoformat()}
    db.add(channel); db.commit(); db.close()

    response = _client(factory).post(f"/api/commerce/actions/{action_id}/measure")
    app.dependency_overrides.pop(get_session, None)
    assert response.status_code == 422
    assert "only reached back to" in response.json()["detail"]


def test_measuring_twice_does_not_change_the_answer(store, monkeypatch):
    """A retry, a second scheduler and an impatient seller are the same problem."""
    factory, action_id = store
    _confirm_and_backdate(factory, action_id, monkeypatch, days_ago=16)
    client = _client(factory)
    first = client.post(f"/api/commerce/actions/{action_id}/measure").json()
    second = client.post(f"/api/commerce/actions/{action_id}/measure").json()
    app.dependency_overrides.pop(get_session, None)
    assert first["measured_at"] == second["measured_at"]
    assert first["impact"] == second["impact"]


# --- isolation --------------------------------------------------------------

def test_another_tenants_action_is_not_found(store, monkeypatch):
    """store_id is checked before anything else happens."""
    factory, action_id = store
    db = factory()
    row = db.get(models.OperatorAction, __import__("uuid").UUID(action_id))
    other = models.Store(user_id=row.store_id, marketplace="US")
    db.add(other); db.flush()
    row.store_id = other.id
    db.add(row); db.commit(); db.close()

    response = _call(factory, f"/api/commerce/actions/{action_id}/confirm",
                     reads=30, monkeypatch=monkeypatch)
    assert response.status_code == 404


# --- what the seller is shown while they wait --------------------------------

def test_an_applied_action_is_visible_while_it_waits(store, monkeypatch):
    """The bug this closes: the count of what was "in effect, waiting to be
    priced" was taken after the list had been filtered down to proven money. An
    action awaiting its first measurement is by definition not proven, so it was
    filtered out of the one screen that existed to show it. Somebody confirmed a
    restock and watched it disappear."""
    factory, action_id = store
    _confirm_and_backdate(factory, action_id, monkeypatch, days_ago=6)
    payroll = _client(factory).get("/api/dashboard/payroll?days=30").json()
    app.dependency_overrides.pop(get_session, None)

    assert payroll["awaiting_measurement"] == 1
    assert len(payroll["awaiting"]) == 1


def test_the_wait_says_when_it_ends_and_what_was_confirmed(store, monkeypatch):
    """Not "waiting", which somebody can do nothing with: the stock Shopify
    confirmed, the day it started and the day the answer arrives."""
    factory, action_id = store
    _confirm_and_backdate(factory, action_id, monkeypatch, days_ago=6)
    payroll = _client(factory).get("/api/dashboard/payroll?days=30").json()
    app.dependency_overrides.pop(get_session, None)

    waiting = payroll["awaiting"][0]
    assert waiting["action_type"] == "RESTOCK_PRODUCT"
    assert waiting["stock_before"] == 4 and waiting["verified_on_hand"] == 30
    assert waiting["state"] == "window_open"
    assert waiting["days_remaining"] == 9      # 14 requested, 5 full days elapsed
    assert waiting["measure_on"] > waiting["window_last"]
    assert waiting["data_state"] == "collecting"


def test_a_wait_caused_by_missing_data_says_so_rather_than_be_patient(
        store, monkeypatch):
    """A window that closed while nothing was syncing is a different situation
    from a window still running, and telling somebody to wait is wrong advice."""
    factory, action_id = store
    _confirm_and_backdate(factory, action_id, monkeypatch, days_ago=16, cover=False)
    db = factory()
    channel = db.scalar(select(models.ChannelConnection))
    channel.synced_at = None
    channel.settings = {}
    db.add(channel); db.commit(); db.close()

    payroll = _client(factory).get("/api/dashboard/payroll?days=30").json()
    app.dependency_overrides.pop(get_session, None)

    waiting = payroll["awaiting"][0]
    assert waiting["state"] == "awaiting_data"
    assert waiting["data_state"] == "incomplete"
    assert "never completed a sync" in waiting["reason"]


def test_a_measured_action_leaves_the_waiting_list_for_the_ledger(store, monkeypatch):
    """It goes into history rather than vanishing: still listed under /api/actions
    with its result, just no longer described as pending."""
    factory, action_id = store
    _confirm_and_backdate(factory, action_id, monkeypatch, days_ago=16)
    client = _client(factory)
    client.post(f"/api/commerce/actions/{action_id}/measure")
    payroll = client.get("/api/dashboard/payroll?days=30").json()
    actions = client.get("/api/actions").json()["actions"]
    app.dependency_overrides.pop(get_session, None)

    assert payroll["awaiting"] == [] and payroll["awaiting_measurement"] == 0
    measured = [a for a in actions if a["id"] == action_id]
    assert measured and measured[0]["status"] == "measured"
    assert measured[0]["impact"]["attributable_units"] == 10
    assert measured[0]["impact"]["net"] == 400.0
