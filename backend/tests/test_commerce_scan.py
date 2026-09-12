"""
Tests for the commerce scan route.

The engine decides what is true; this decides what a seller ends up looking at.
The failures worth guarding are the ones that waste attention or borrow
credibility: a list that grows every time somebody presses the button, and
advice derived from a demo store presented beside advice about real money.
"""
import datetime as dt
import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import commerce_engine
from app.db import crud, models
from app.db.models import Base
from app.db.session import get_session
from app.main import app


def _store_with(days: int, *, revenue: float = 40.0, orders: int = 1,
                costs_complete: bool = False, demo: bool = False,
                channels: int = 1):
    """A tenant with `channels` connected shops and `days` of daily metrics."""
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    db = factory()
    store = crud.get_or_create_dev_store(db)
    today = dt.datetime.now(dt.timezone.utc).date()
    for index in range(channels):
        name = f"DEMO-{index}" if demo else f"shop-{index}.myshopify.com"
        channel = models.ChannelConnection(
            store_id=store.id, provider="shopify", external_account_id=name,
            display_name=name, status="connected", currency="USD",
            settings={"demo": True} if demo else {},
        )
        db.add(channel); db.flush()
        for back in range(days):
            db.add(models.CommerceDailyMetric(
                store_id=store.id, channel_id=channel.id,
                metric_date=today - dt.timedelta(days=back),
                revenue=Decimal(str(revenue)), refunds=0, fees=0, landed_cogs=0,
                advertising_spend=0, orders=orders, units=orders, sessions=0,
                currency="USD", source="shopify_graphql", costs_complete=costs_complete,
            ))
    db.commit(); db.close()
    return factory


def _scan(factory, times: int = 1):
    def override():
        session = factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override
    try:
        client = TestClient(app)
        results = [client.post("/api/commerce/scan").json() for _ in range(times)]
    finally:
        app.dependency_overrides.pop(get_session, None)
    return results[-1] if times == 1 else results


def _actions(factory):
    db = factory()
    try:
        return list(db.scalars(select(models.OperatorAction)))
    finally:
        db.close()


def test_a_real_store_without_costs_gets_one_proposal():
    result = _scan(_store_with(10))
    assert result["findings"] == 1
    assert result["opened"] == 1
    assert result["observed_days"] == 10


def test_the_opened_action_is_the_costs_one_and_claims_no_money():
    factory = _store_with(10)
    _scan(factory)
    actions = _actions(factory)
    assert len(actions) == 1
    action = actions[0]
    assert action.module == "commerce"
    assert action.action_type == "ENTER_LANDED_COSTS"
    assert action.status == "proposed"
    # The finding is measured; acting on it has not been, and only a measured
    # outcome may ever reach payroll.
    assert action.evidence_mode == "unverified"
    assert action.projected_impact is None, "zero would read as nothing at stake"
    assert action.baseline["days"] == 10


def test_scanning_twice_does_not_duplicate_the_list():
    """A seller pressing the button again should not be punished for it."""
    factory = _store_with(10)
    first, second = _scan(factory, times=2)
    assert first["opened"] == 1 and first["already_open"] == 0
    assert second["opened"] == 0 and second["already_open"] == 1
    assert len(_actions(factory)) == 1


def test_a_store_with_complete_costs_is_left_alone():
    factory = _store_with(10, costs_complete=True)
    assert _scan(factory)["findings"] == 0
    assert _actions(factory) == []


def test_a_store_younger_than_a_week_is_not_advised():
    factory = _store_with(4)
    assert _scan(factory)["findings"] == 0
    assert _actions(factory) == []
    db = factory()
    try:
        event = db.scalar(select(models.AuditEvent).where(
            models.AuditEvent.action == "commerce.scan"))
        assert event is not None
        assert event.after["opened"] == 0
        assert event.after["observed_days"] == 4
    finally:
        db.close()


def test_a_demo_channel_never_produces_a_proposal():
    """Advice derived from synthetic sales would sit in the same list as advice
    about real money, and look exactly like it."""
    factory = _store_with(10, demo=True)
    result = _scan(factory)
    assert result["findings"] == 0 and result["opened"] == 0
    assert _actions(factory) == []


def test_two_channels_on_one_day_are_counted_once_per_day():
    """Two shops selling the same day must not make the window look twice as
    long, or every per-day figure derived from it is halved."""
    factory = _store_with(10, channels=2)
    result = _scan(factory)
    assert result["observed_days"] == 10
    action = _actions(factory)[0]
    assert action.baseline["days"] == 10
    assert action.baseline["revenue"] == 800.0   # 2 channels * 10 days * 40.00


def test_only_the_physical_product_is_proposed_for_restocking():
    """End to end, through the tables the sync writes.

    Both products sold and both are down to nothing. One is a gift card, which
    the live store's own catalogue reported as tracked, bought twice and minus
    two in stock — every sum said restock it. Only the snowboard may appear.
    """
    engine_ = create_engine("sqlite://", poolclass=StaticPool,
                            connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine_)
    factory = sessionmaker(bind=engine_, expire_on_commit=False)
    db = factory()
    store = crud.get_or_create_dev_store(db)
    channel = models.ChannelConnection(
        store_id=store.id, provider="shopify", external_account_id="mixed.myshopify.com",
        display_name="mixed", status="connected", currency="USD", settings={},
    )
    db.add(channel); db.flush()
    today = dt.datetime.now(dt.timezone.utc).date()
    catalogue = [
        ("gid://card", "Gift Card", True, False, -2),
        ("gid://board", "The Minimal Snowboard", False, True, 1),
    ]
    for product_id, title, is_gift_card, ships, on_hand in catalogue:
        db.add(models.ChannelProduct(
            store_id=store.id, channel_id=channel.id, external_product_id=product_id,
            title=title, status="ACTIVE", on_hand=on_hand,
            is_gift_card=is_gift_card, requires_shipping=ships,
        ))
        for back in range(10):
            day = today - dt.timedelta(days=back)
            db.add(models.ProductDailyMetric(
                store_id=store.id, channel_id=channel.id,
                external_product_id=product_id, metric_date=day,
                units=1, revenue=Decimal("20.00"), currency="USD",
                source="shopify_graphql",
            ))
            db.add(models.CommerceDailyMetric(
                store_id=store.id, channel_id=channel.id, metric_date=day,
                revenue=Decimal("40.00"), refunds=0, fees=0, landed_cogs=0,
                advertising_spend=0, orders=2, units=2, sessions=0, currency="USD",
                source="shopify_graphql", costs_complete=True,
            ) if product_id == "gid://card" else models.ProductDailyMetric(
                store_id=store.id, channel_id=channel.id,
                external_product_id=product_id + "-noop", metric_date=day,
                units=0, revenue=Decimal("0"), currency="USD", source="shopify_graphql",
            ))
    db.commit(); db.close()

    _scan(factory)
    restocks = [row for row in _actions(factory) if row.action_type == "RESTOCK_PRODUCT"]
    assert [row.target for row in restocks] == ["The Minimal Snowboard"]


def test_a_store_with_no_channel_is_not_scanned():
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    db = factory(); crud.get_or_create_dev_store(db); db.commit(); db.close()

    result = _scan(factory)
    assert result == {"days": 30, "observed_days": 0, "findings": 0,
                      "opened": 0, "already_open": 0}


def test_the_scan_reads_the_same_series_the_chart_does():
    """The stall rule counts silent days. If the scan built its own series and
    dropped them, a stalled store would look busy to it and quiet on screen."""
    import inspect
    from app import commerce_service
    from app.routers import dashboards

    for module in (commerce_service, dashboards):
        assert "commerce_engine.daily_series" in inspect.getsource(module), \
            f"{module.__name__} must not build its own day series"
    assert callable(commerce_engine.daily_series)


def test_the_sync_scans_without_being_asked():
    """A seller asked why they had to press a button to make the Operator look.

    They did because nothing ever scanned on its own: the worker runs with a
    scheduler and nothing was ever put in it. The sync now re-reads what it just
    wrote, so a proposal appears because sales arrived, not because somebody
    remembered to ask.
    """
    import inspect
    from app import tasks

    source = inspect.getsource(tasks.run_shopify_sync)
    assert "scan_store" in source, "a finished sync must re-read what it wrote"
