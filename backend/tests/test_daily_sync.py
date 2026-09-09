"""Queueing the daily read of every connected shop.

The gap this closes: a sync only ever happened when somebody pressed a button or
had just finished connecting. A shop left alone stopped producing days, and
because a missing day looks exactly like a quiet day, nothing anywhere said so.

Nothing here reaches Shopify. The queue is replaced at its boundary, so a
"queued" job is an assertion rather than a side effect, and the sync itself —
which is what would talk to a real shop — is never run by these tests.
"""
import base64
import datetime as dt
import json
import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import actions_engine, credentials, tasks
from app.db import crud, models
from app.db.models import Base

PRODUCT = "gid://shopify/Product/1"
NOW = dt.datetime(2026, 9, 23, 7, 0, tzinfo=dt.timezone.utc)


@pytest.fixture(autouse=True)
def _configured(monkeypatch):
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEYS",
                       json.dumps({"v1": base64.b64encode(b"k" * 32).decode()}))
    monkeypatch.setenv("CREDENTIAL_ACTIVE_KEY_VERSION", "v1")


@pytest.fixture
def db():
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    yield session
    session.close()


@pytest.fixture
def queued(db, monkeypatch):
    """Every enqueue lands here instead of in Redis."""
    jobs = []
    monkeypatch.setattr(tasks, "SessionLocal", lambda: db)
    monkeypatch.setattr("app.db.session.SessionLocal", lambda: db)
    monkeypatch.setattr("app.queueing.enqueue_shopify_sync",
                        lambda **kw: jobs.append(kw) or "job-1")
    # Every shop has a readable credential unless a test says otherwise.
    monkeypatch.setattr(credentials, "load_credential", lambda *a, **k: "shpat_stub")
    return jobs


def _shop(db, *, status="connected", synced_through=None, synced_at=None):
    user = models.User(email=f"{uuid.uuid4().hex[:8]}@example.test")
    db.add(user); db.flush()
    store = models.Store(user_id=user.id, marketplace="US")
    db.add(store); db.flush()
    settings = {}
    if synced_through:
        settings["synced_through"] = synced_through.isoformat()
    channel = models.ChannelConnection(
        store_id=store.id, provider="shopify",
        external_account_id=f"{uuid.uuid4().hex[:6]}.myshopify.com",
        display_name="shop", status=status, currency="USD",
        settings=settings, synced_at=synced_at,
    )
    db.add(channel); db.commit()
    return store, channel


# --- who gets read ----------------------------------------------------------

def test_a_connected_shop_is_queued(db, queued):
    _shop(db)
    result = tasks.sync_connected_shopify_stores(now=NOW)
    assert result["queued"] == 1 and len(queued) == 1


@pytest.mark.parametrize("status", ["revoked", "disconnected", "pending"])
def test_only_a_connected_shop_is_read(db, queued, status):
    _shop(db, status=status)
    assert tasks.sync_connected_shopify_stores(now=NOW)["queued"] == 0
    assert queued == []


def test_a_shop_whose_credential_cannot_be_read_is_skipped_not_queued(
        db, queued, monkeypatch):
    """A rotated keyring is not a transient fault. Queueing the job would spend
    a worker on reaching the same conclusion with less context."""
    _shop(db)
    monkeypatch.setattr(credentials, "load_credential", lambda *a, **k: None)
    result = tasks.sync_connected_shopify_stores(now=NOW)
    assert result["queued"] == 0 and result["skipped"] == 1
    assert "no readable credential" in result["reasons"]


def test_every_shop_is_queued_under_its_own_tenant(db, queued):
    mine, _ = _shop(db)
    theirs, _ = _shop(db)
    tasks.sync_connected_shopify_stores(now=NOW)
    assert {job["tenant_id"] for job in queued} == {str(mine.id), str(theirs.id)}


def test_one_broken_shop_does_not_stop_the_others(db, queued, monkeypatch):
    broken, broken_channel = _shop(db)
    _shop(db)

    real_claim = crud.claim_idempotency

    def explode(session, *, store_id, operation, key):
        if store_id == broken.id:
            raise RuntimeError("something specific to this tenant")
        return real_claim(session, store_id=store_id, operation=operation, key=key)

    monkeypatch.setattr(crud, "claim_idempotency", explode)
    result = tasks.sync_connected_shopify_stores(now=NOW)
    assert result["queued"] == 1 and result["failed"] == 1


# --- how far back -----------------------------------------------------------

def test_a_shop_read_yesterday_is_still_read_generously(db, queued):
    """Re-reading a day that is already right costs one upsert. Missing one
    costs a day of somebody's sales with no sign that anything is wrong."""
    _shop(db, synced_through=NOW.date() - dt.timedelta(days=1))
    tasks.sync_connected_shopify_stores(now=NOW)
    assert queued[0]["days"] == tasks.DAILY_SYNC_DAYS


def test_a_gap_is_covered_rather_than_stepped_over(db, queued):
    """Twenty days since anything was read, so the range reaches back over all
    of them instead of fetching the last week and leaving a hole."""
    _shop(db, synced_through=NOW.date() - dt.timedelta(days=20))
    tasks.sync_connected_shopify_stores(now=NOW)
    assert queued[0]["days"] == 22


def test_the_range_is_capped_where_the_sync_itself_refuses(db, queued):
    """run_shopify_sync refuses more than 60 days, so asking for more would
    guarantee a failed job rather than a longer read."""
    _shop(db, synced_through=NOW.date() - dt.timedelta(days=400))
    tasks.sync_connected_shopify_stores(now=NOW)
    assert queued[0]["days"] == tasks.MAX_SYNC_DAYS


def test_the_range_reaches_back_to_cover_an_open_measurement_window(db, queued):
    """A result waiting on days nobody fetched waits for ever. The sync that
    could fetch them is the one that has to know they are wanted."""
    store, channel = _shop(db, synced_through=NOW.date() - dt.timedelta(days=1))
    db.add(models.OperatorAction(
        store_id=store.id, module="commerce", action_type="RESTOCK_PRODUCT",
        target="Snowboard", status=actions_engine.ActionStatus.APPLIED.value,
        evidence_mode="unverified", baseline={"on_hand": 4, "product_id": PRODUCT},
        currency="USD", measurement_days=14, applied_by="human",
        applied_at=NOW - dt.timedelta(days=13)))
    db.commit()

    tasks.sync_connected_shopify_stores(now=NOW)
    # The window opened twelve days ago; the read has to reach that far.
    assert queued[0]["days"] >= 13


# --- running it twice -------------------------------------------------------

def _worker_finishes(db):
    """What the worker does after the job is picked up. Without it the shop
    stays correctly blocked as still-syncing, which is a different test."""
    for record in db.scalars(select(models.IdempotencyRecord)):
        crud.complete_idempotency(db, record, {"orders": 0})


def test_the_same_day_twice_queues_once(db, queued):
    """A retry, a second region and somebody running it by hand are the same
    problem, and the answer is a key the database refuses to duplicate."""
    _shop(db)
    first = tasks.sync_connected_shopify_stores(now=NOW)
    _worker_finishes(db)
    second = tasks.sync_connected_shopify_stores(now=NOW)
    assert first["queued"] == 1 and second["queued"] == 0
    assert "already queued today" in second["reasons"]
    assert len(queued) == 1


def test_a_sync_still_running_when_the_next_day_fires_is_left_alone(db, queued):
    """Yesterday's read has not finished. Starting today's alongside it would be
    two full reads of one shop for one set of numbers."""
    _shop(db)
    tasks.sync_connected_shopify_stores(now=NOW)
    result = tasks.sync_connected_shopify_stores(now=NOW + dt.timedelta(days=1))
    assert result["queued"] == 0
    assert "a sync is already running" in result["reasons"]


def test_tomorrow_is_a_new_read(db, queued):
    _shop(db)
    tasks.sync_connected_shopify_stores(now=NOW)
    _worker_finishes(db)
    tasks.sync_connected_shopify_stores(now=NOW + dt.timedelta(days=1))
    assert len(queued) == 2


def test_a_sync_already_running_is_not_joined_by_a_second(db, queued):
    """Two full reads of one shop for one set of numbers, and the second teaches
    the first's rate limit a lesson."""
    store, channel = _shop(db)
    crud.claim_idempotency(db, store_id=store.id,
                           operation=f"shopify.sync:{channel.id}:30",
                           key="a-person-pressed-the-button")
    result = tasks.sync_connected_shopify_stores(now=NOW)
    assert result["queued"] == 0
    assert "a sync is already running" in result["reasons"]


def test_a_claim_left_by_a_dead_process_does_not_block_tomorrow(db, queued):
    """Otherwise one crashed worker stops a shop being read ever again."""
    store, channel = _shop(db)
    record, _ = crud.claim_idempotency(
        db, store_id=store.id, operation=f"shopify.sync:{channel.id}:30",
        key="the-worker-that-died")
    record.created_at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=2)
    db.add(record); db.commit()

    assert tasks.sync_connected_shopify_stores(now=NOW)["queued"] == 1


def test_a_dry_run_queues_nothing_and_claims_nothing(db, queued):
    _shop(db)
    result = tasks.sync_connected_shopify_stores(now=NOW, dry_run=True)
    assert result["queued"] == 1        # would have
    assert queued == []                 # did not
    assert db.scalar(select(models.IdempotencyRecord)) is None


# --- the two ways in have to agree ------------------------------------------

ORDER_DAY = dt.date(2026, 9, 20)


class _FakeShopify:
    """Shopify's half of a sync: two orders on one day, one product."""

    def __init__(self, *a, **k):
        pass

    def order_pages(self, created_at_min=None):
        yield [{
            "createdAt": f"{ORDER_DAY.isoformat()}T10:00:00Z",
            "currentTotalPriceSet": {"shopMoney": {"amount": "120.00",
                                                   "currencyCode": "USD"}},
            "lineItems": {"nodes": [{
                "quantity": 2,
                "product": {"id": PRODUCT, "title": "Snowboard"},
                "originalTotalSet": {"shopMoney": {"amount": "120.00"}},
            }]},
        }, {
            "createdAt": f"{ORDER_DAY.isoformat()}T18:00:00Z",
            "currentTotalPriceSet": {"shopMoney": {"amount": "60.00",
                                                   "currencyCode": "USD"}},
            "lineItems": {"nodes": [{
                "quantity": 1,
                "product": {"id": PRODUCT, "title": "Snowboard"},
                "originalTotalSet": {"shopMoney": {"amount": "60.00"}},
            }]},
        }]

    def product_pages(self):
        yield [{"id": PRODUCT, "title": "Snowboard", "status": "ACTIVE",
                "tracksInventory": True, "totalInventory": 30,
                "isGiftCard": False,
                "variants": {"nodes": [{"inventoryItem": {"requiresShipping": True}}]}}]

    def close(self):
        pass


def _run_a_sync(db, store, channel, monkeypatch, *, operation, key, days=30):
    """Perform one sync the way a worker would, with Shopify stubbed."""
    from app import shopify

    monkeypatch.setattr(shopify, "AdminGraphQLClient", _FakeShopify)
    monkeypatch.setattr(tasks, "SessionLocal", lambda: db)
    record, _ = crud.claim_idempotency(db, store_id=store.id, operation=operation,
                                       key=key)
    return tasks.run_shopify_sync(
        tenant_id=str(store.id), actor_id=str(store.user_id),
        channel_id=str(channel.id), days=days,
        idempotency_record_id=str(record.id))


def _aggregates(db, store):
    daily = [(m.metric_date, float(m.revenue), m.orders, m.units)
             for m in db.scalars(select(models.CommerceDailyMetric).where(
                 models.CommerceDailyMetric.store_id == store.id))]
    per_product = [(m.metric_date, m.external_product_id, m.units, float(m.revenue))
                   for m in db.scalars(select(models.ProductDailyMetric).where(
                       models.ProductDailyMetric.store_id == store.id))]
    return sorted(daily), sorted(per_product)


def test_the_manual_and_the_scheduled_sync_produce_the_same_numbers(db, monkeypatch):
    """They must, because they are the same job — and this is what proves the
    scheduled path did not quietly grow a second implementation."""
    store, channel = _shop(db)
    monkeypatch.setattr(credentials, "load_credential", lambda *a, **k: "shpat_stub")

    _run_a_sync(db, store, channel, monkeypatch,
                operation=f"shopify.sync:{channel.id}:30", key="a-person-pressed-it")
    by_hand = _aggregates(db, store)

    _run_a_sync(db, store, channel, monkeypatch,
                operation=f"shopify.sync:{channel.id}:30",
                key=f"scheduled:2026-09-23:{channel.id}")
    by_schedule = _aggregates(db, store)

    assert by_hand == by_schedule
    assert by_hand[0] == [(ORDER_DAY, 180.0, 2, 3)]
    assert by_hand[1] == [(ORDER_DAY, PRODUCT, 3, 180.0)]


def test_running_the_sync_again_does_not_double_a_day(db, monkeypatch):
    """Every write is an upsert keyed on the day. A range that overlaps the last
    one is the normal case, not an accident to be avoided."""
    store, channel = _shop(db)
    monkeypatch.setattr(credentials, "load_credential", lambda *a, **k: "shpat_stub")

    for attempt in range(3):
        _run_a_sync(db, store, channel, monkeypatch,
                    operation=f"shopify.sync:{channel.id}:30", key=f"run-{attempt}")

    daily, per_product = _aggregates(db, store)
    assert daily == [(ORDER_DAY, 180.0, 2, 3)]
    assert per_product == [(ORDER_DAY, PRODUCT, 3, 180.0)]


def test_a_sync_corrects_a_day_a_webhook_had_already_counted(db, monkeypatch):
    """The webhook adds to a day as orders arrive; the sync overwrites it with
    what Shopify says the day actually was. The sync is the authority, so a
    delivery that arrived twice is corrected rather than carried for ever."""
    store, channel = _shop(db)
    monkeypatch.setattr(credentials, "load_credential", lambda *a, **k: "shpat_stub")
    db.add(models.CommerceDailyMetric(
        store_id=store.id, channel_id=channel.id, metric_date=ORDER_DAY,
        revenue=999.0, refunds=0, fees=0, landed_cogs=0, advertising_spend=0,
        orders=9, units=9, sessions=0, currency="USD", source="shopify_webhook",
        costs_complete=False))
    db.commit()

    _run_a_sync(db, store, channel, monkeypatch,
                operation=f"shopify.sync:{channel.id}:30", key="the-correction")

    daily, _ = _aggregates(db, store)
    assert daily == [(ORDER_DAY, 180.0, 2, 3)]


def test_the_sync_records_which_days_it_read(db, monkeypatch):
    """Without this a measurement window cannot be shown to be covered, and
    "the shop was synced afterwards" does not say how far back it reached."""
    store, channel = _shop(db)
    monkeypatch.setattr(credentials, "load_credential", lambda *a, **k: "shpat_stub")

    _run_a_sync(db, store, channel, monkeypatch,
                operation=f"shopify.sync:{channel.id}:30", key="range-check", days=30)

    channel = db.get(models.ChannelConnection, channel.id)
    covered_from = dt.date.fromisoformat(channel.settings["synced_from"])
    # Against the UTC date, because that is what the sync subtracts from. Local
    # is a different day for part of every night, and this assertion would then
    # pass or fail depending on the hour it ran.
    today = dt.datetime.now(dt.timezone.utc).date()
    assert (today - covered_from).days == 30


def test_the_sync_stores_no_customer_details(db, monkeypatch):
    """Daily aggregates only. An order payload is somebody's name and address,
    and this product has no reason to hold either."""
    store, channel = _shop(db)
    monkeypatch.setattr(credentials, "load_credential", lambda *a, **k: "shpat_stub")

    _run_a_sync(db, store, channel, monkeypatch,
                operation=f"shopify.sync:{channel.id}:30", key="privacy-check")

    for table in (models.CommerceDailyMetric, models.ProductDailyMetric):
        for row in db.scalars(select(table).where(table.store_id == store.id)):
            columns = {c.name for c in table.__table__.columns}
            assert not columns & {"customer_email", "customer_name", "payload",
                                  "raw", "address"}


# --- what the richer order fields are for ------------------------------------

SMALL = "gid://shopify/ProductVariant/10"
LARGE = "gid://shopify/ProductVariant/20"


def _line(*, line_id, variant, title, quantity, original, discounted, tax="0.00"):
    return {
        "id": line_id, "quantity": quantity,
        "product": {"id": PRODUCT, "title": "Snowboard"},
        "variant": {"id": variant, "title": title},
        "originalTotalSet": {"shopMoney": {"amount": original}},
        "discountedTotalSet": {"shopMoney": {"amount": discounted,
                                             "currencyCode": "USD"}},
        "taxLines": [{"priceSet": {"shopMoney": {"amount": tax}}}],
    }


def _order(*, lines, taxes_included=False, cancelled=False, refunds=None,
           total="0.00"):
    return {
        "id": f"gid://shopify/Order/{uuid.uuid4().hex[:6]}",
        "createdAt": f"{ORDER_DAY.isoformat()}T10:00:00Z",
        "cancelledAt": "2026-09-20T11:00:00Z" if cancelled else None,
        "currencyCode": "USD", "taxesIncluded": taxes_included,
        "currentTotalPriceSet": {"shopMoney": {"amount": total,
                                               "currencyCode": "USD"}},
        "lineItems": {"nodes": lines},
        "refunds": refunds or [],
    }


class _DetailedShopify:
    """Shopify answering the full query, with whatever orders a test sets."""

    orders: list = []
    variants: list = [
        {"id": SMALL, "title": "Small",
         "inventoryItem": {"requiresShipping": True,
                           "unitCost": {"amount": "60.00", "currencyCode": "USD"}}},
    ]

    def __init__(self, *a, **k):
        self.unavailable = set()

    def order_pages(self, created_at_min=None):
        yield list(self.orders)

    def product_pages(self):
        yield [{"id": PRODUCT, "title": "Snowboard", "status": "ACTIVE",
                "tracksInventory": True, "totalInventory": 30, "isGiftCard": False,
                "variants": {"nodes": list(self.variants)}}]

    def close(self):
        pass


def _sync_with(db, monkeypatch, orders, *, variants=None):
    from app import shopify

    store, channel = _shop(db)
    monkeypatch.setattr(credentials, "load_credential", lambda *a, **k: "shpat_stub")
    client = type("C", (_DetailedShopify,), {
        "orders": orders,
        "variants": variants if variants is not None else _DetailedShopify.variants,
    })
    monkeypatch.setattr(shopify, "AdminGraphQLClient", client)
    monkeypatch.setattr(tasks, "SessionLocal", lambda: db)
    record, _ = crud.claim_idempotency(db, store_id=store.id,
                                       operation=f"shopify.sync:{channel.id}:30",
                                       key=f"detail-{uuid.uuid4().hex[:6]}")
    tasks.run_shopify_sync(tenant_id=str(store.id), actor_id=str(store.user_id),
                           channel_id=str(channel.id), days=30,
                           idempotency_record_id=str(record.id))
    return store, channel


def _variant_rows(db, store):
    return {row.external_variant_id: row for row in db.scalars(
        select(models.VariantDailyMetric).where(
            models.VariantDailyMetric.store_id == store.id))}


def test_each_variant_gets_its_own_row(db, queued, monkeypatch):
    """Margin is a per-variant question: two variants of one snowboard can cost
    different amounts to buy."""
    store, _ = _sync_with(db, monkeypatch, [_order(lines=[
        _line(line_id="l1", variant=SMALL, title="Small", quantity=2,
              original="200.00", discounted="200.00"),
        _line(line_id="l2", variant=LARGE, title="Large", quantity=1,
              original="300.00", discounted="300.00"),
    ], total="500.00")])

    rows = _variant_rows(db, store)
    assert set(rows) == {SMALL, LARGE}
    assert rows[SMALL].units == 2 and float(rows[SMALL].revenue) == 200.0
    assert rows[LARGE].variant_title == "Large"


def test_a_discount_is_taken_off_before_anything_is_claimed(db, queued, monkeypatch):
    """originalTotalSet would credit a restock with money nobody paid."""
    store, _ = _sync_with(db, monkeypatch, [_order(lines=[
        _line(line_id="l1", variant=SMALL, title="Small", quantity=2,
              original="200.00", discounted="150.00"),
    ], total="150.00")])
    assert float(_variant_rows(db, store)[SMALL].revenue) == 150.0


def test_tax_comes_off_when_the_shop_prices_include_it(db, queued, monkeypatch):
    store, _ = _sync_with(db, monkeypatch, [_order(taxes_included=True, lines=[
        _line(line_id="l1", variant=SMALL, title="Small", quantity=1,
              original="120.00", discounted="120.00", tax="20.00"),
    ], total="120.00")])
    assert float(_variant_rows(db, store)[SMALL].revenue) == 100.0


def test_tax_stays_out_of_it_when_prices_exclude_tax(db, queued, monkeypatch):
    """The tax was never inside this figure, and subtracting it would understate
    the sale."""
    store, _ = _sync_with(db, monkeypatch, [_order(taxes_included=False, lines=[
        _line(line_id="l1", variant=SMALL, title="Small", quantity=1,
              original="100.00", discounted="100.00", tax="20.00"),
    ], total="120.00")])
    assert float(_variant_rows(db, store)[SMALL].revenue) == 100.0


def test_a_refund_is_recorded_against_the_day_of_the_sale(db, queued, monkeypatch):
    """A unit that came back was not one this restock sold."""
    store, _ = _sync_with(db, monkeypatch, [_order(lines=[
        _line(line_id="l1", variant=SMALL, title="Small", quantity=3,
              original="300.00", discounted="300.00"),
    ], refunds=[{"createdAt": "2026-09-25T10:00:00Z",
                 "refundLineItems": {"nodes": [
                     {"quantity": 1, "lineItem": {"id": "l1"},
                      "subtotalSet": {"shopMoney": {"amount": "100.00"}}}]}}],
        total="300.00")])

    row = _variant_rows(db, store)[SMALL]
    assert row.units == 3 and row.refunded_units == 1
    assert float(row.refunded_revenue) == 100.0


def test_a_cancelled_order_never_reaches_the_numbers(db, queued, monkeypatch):
    store, _ = _sync_with(db, monkeypatch, [
        _order(lines=[_line(line_id="l1", variant=SMALL, title="Small", quantity=5,
                            original="500.00", discounted="500.00")],
               cancelled=True, total="500.00"),
    ])
    assert _variant_rows(db, store) == {}


def test_a_line_with_no_variant_is_kept_but_never_priced(db, queued, monkeypatch):
    """Known to have sold, not known as what. Attaching a neighbour's cost would
    be a guess wearing a measurement's clothes."""
    from app import product_costs as pc

    line = _line(line_id="l1", variant=SMALL, title="Small", quantity=2,
                 original="200.00", discounted="200.00")
    line.pop("variant")
    store, _ = _sync_with(db, monkeypatch, [_order(lines=[line], total="200.00")])

    rows = _variant_rows(db, store)
    key = pc.unknown_variant_key(PRODUCT)
    assert key in rows and rows[key].units == 2
    assert pc.cost_on(db, store_id=store.id, product_id=PRODUCT,
                      variant_id=key, on=ORDER_DAY) is None


# --- the cost that comes with the products -----------------------------------

def test_a_unit_cost_from_shopify_is_recorded_with_its_provenance(db, queued,
                                                                  monkeypatch):
    from app import product_costs as pc

    store, _ = _sync_with(db, monkeypatch, [])
    cost = pc.cost_on(db, store_id=store.id, product_id=PRODUCT, variant_id=SMALL,
                      on=dt.date.today())
    assert cost is not None
    assert cost.amount == __import__("decimal").Decimal("60.0000")
    assert cost.source == pc.SHOPIFY and cost.verification == pc.CONFIRMED


def test_a_variant_shopify_has_no_cost_for_gets_no_row(db, queued, monkeypatch):
    """Not filled in is unknown. Writing a zero would turn "we do not know" into
    a margin equal to the whole sale price."""
    from app import product_costs as pc

    store, _ = _sync_with(db, monkeypatch, [], variants=[
        {"id": SMALL, "title": "Small",
         "inventoryItem": {"requiresShipping": True, "unitCost": None}}])
    assert pc.history(db, store_id=store.id) == []


def test_running_the_sync_again_does_not_pile_up_cost_rows(db, queued, monkeypatch):
    from app import product_costs as pc

    store, channel = _sync_with(db, monkeypatch, [])
    _worker_finishes(db)
    record, _ = crud.claim_idempotency(db, store_id=store.id,
                                       operation=f"shopify.sync:{channel.id}:30",
                                       key="second-run")
    tasks.run_shopify_sync(tenant_id=str(store.id), actor_id=str(store.user_id),
                           channel_id=str(channel.id), days=30,
                           idempotency_record_id=str(record.id))
    assert len(pc.history(db, store_id=store.id)) == 1


# --- a shop that will not answer the new fields ------------------------------

def _refusing_execute(refused_fields):
    """Shopify answering as an older shop would: the detailed fields do not
    exist, everything else does.

    Patched at `execute` rather than at the client, so the fallback under test
    is the real one in `order_pages` and `product_pages` rather than a fake
    standing in for it.
    """
    from app import shopify

    def execute(self, query, variables):
        if any(field in query for field in refused_fields):
            raise shopify.ShopifyFieldUnavailable(
                "undefinedField: Field 'unitCost' doesn't exist on type 'InventoryItem'")
        if "orders(" in query:
            return {"orders": {"nodes": [], "pageInfo": {"hasNextPage": False}}}
        return {"products": {"nodes": [], "pageInfo": {"hasNextPage": False}}}

    return execute


def test_a_shop_that_refuses_the_new_fields_still_syncs(db, queued, monkeypatch):
    """Losing a margin is a smaller harm than losing the orders, and the
    connection must not break over a field."""
    from app import shopify

    store, channel = _shop(db)
    monkeypatch.setattr(credentials, "load_credential", lambda *a, **k: "shpat_stub")
    monkeypatch.setattr(shopify.AdminGraphQLClient, "execute",
                        _refusing_execute(("unitCost", "discountedTotalSet")))
    monkeypatch.setattr(tasks, "SessionLocal", lambda: db)
    record, _ = crud.claim_idempotency(db, store_id=store.id,
                                       operation=f"shopify.sync:{channel.id}:30",
                                       key="older-shop")
    result = tasks.run_shopify_sync(
        tenant_id=str(store.id), actor_id=str(store.user_id),
        channel_id=str(channel.id), days=30,
        idempotency_record_id=str(record.id))

    assert set(result["unavailable"]) == {"order_detail", "unit_cost"}
    channel = db.get(models.ChannelConnection, channel.id)
    assert channel.status == "connected", "a refused field must not break the shop"
    assert set(channel.settings["shopify_unavailable"]) == {"order_detail", "unit_cost"}


def test_a_shop_that_starts_answering_again_stops_being_flagged(db, queued,
                                                                monkeypatch):
    """The note is about what is true now, not what was once refused."""
    from app import shopify

    store, channel = _shop(db)
    channel.settings = {**(channel.settings or {}),
                        "shopify_unavailable": ["unit_cost"]}
    db.add(channel); db.commit()
    monkeypatch.setattr(credentials, "load_credential", lambda *a, **k: "shpat_stub")
    monkeypatch.setattr(shopify, "AdminGraphQLClient", _DetailedShopify)
    monkeypatch.setattr(tasks, "SessionLocal", lambda: db)
    record, _ = crud.claim_idempotency(db, store_id=store.id,
                                       operation=f"shopify.sync:{channel.id}:30",
                                       key="answers-again")
    tasks.run_shopify_sync(tenant_id=str(store.id), actor_id=str(store.user_id),
                           channel_id=str(channel.id), days=30,
                           idempotency_record_id=str(record.id))

    channel = db.get(models.ChannelConnection, channel.id)
    assert "shopify_unavailable" not in channel.settings
