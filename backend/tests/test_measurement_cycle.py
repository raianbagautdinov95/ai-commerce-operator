"""The cycle that has to close without anybody watching it.

    daily sync -> product metrics -> the window ends -> measurement -> evidence

Every test here stands at a chosen moment rather than waiting for one. A test
that needs fourteen real days is a test nobody runs, and the failures being
guarded are all failures of timing: a day counted twice, a window measured a day
early, a result priced from days nothing had read.

Rows are read back by id rather than held as objects. The task owns its session
and rolls it back when it decides to wait, which expires everything the test was
holding — the same thing that happens in production, where the caller and the
job never share a session at all.

Nothing reaches Shopify, Stripe, Resend or the working database.
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

from decimal import Decimal

from app import (actions_engine, commerce_measurement, product_costs, scheduler,
                 tasks)
from app.db import models
from app.db.models import Base

PRODUCT = "gid://shopify/Product/1"
DAY = dt.timedelta(days=1)


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
def bound(db, monkeypatch):
    """Point the task entrypoints at this test's database."""
    monkeypatch.setattr(tasks, "SessionLocal", lambda: db)
    monkeypatch.setattr("app.db.session.SessionLocal", lambda: db)
    return db


def _action(db, action_id) -> models.OperatorAction:
    return db.get(models.OperatorAction, action_id)


def _store(db):
    user = models.User(email=f"{uuid.uuid4().hex[:8]}@example.test")
    db.add(user); db.flush()
    store = models.Store(user_id=user.id, marketplace="US")
    db.add(store); db.flush()
    return store


def _channel(db, store, *, synced_at=None, synced_from=None, status="connected"):
    channel = models.ChannelConnection(
        store_id=store.id, provider="shopify",
        external_account_id=f"{uuid.uuid4().hex[:6]}.myshopify.com",
        display_name="shop", status=status, currency="USD",
        settings={"synced_from": synced_from.isoformat()} if synced_from else {},
        synced_at=synced_at,
    )
    db.add(channel); db.flush()
    return channel


def _applied_restock(db, store, *, applied_at, on_hand=4, measurement_days=14,
                     witnessed=True):
    baseline = {"days": 10, "revenue": 400.0, "spend": 0.0, "on_hand": on_hand,
                "product_id": PRODUCT}
    if witnessed:
        baseline["verified_on_hand"] = 30
        baseline["verified_at"] = applied_at.isoformat()
    row = models.OperatorAction(
        store_id=store.id, module="commerce", action_type="RESTOCK_PRODUCT",
        target="Snowboard", status=actions_engine.ActionStatus.APPLIED.value,
        evidence_mode="unverified", source_type="commerce_daily_metrics",
        source_id=PRODUCT, baseline=baseline, revert_to={"revertible": False},
        currency="USD", measurement_days=measurement_days,
        applied_by="human", applied_at=applied_at,
    )
    db.add(row); db.flush()
    return row


VARIANT = "gid://shopify/ProductVariant/10"


def _sales(db, store, channel, *, start, days, units=1, revenue=60.0,
           variant=VARIANT, currency="USD", product_row=True):
    for offset in range(days):
        day = start + dt.timedelta(days=offset)
        if product_row:
            db.add(models.ProductDailyMetric(
                store_id=store.id, channel_id=channel.id,
                external_product_id=PRODUCT, metric_date=day, units=units,
                revenue=revenue, currency=currency, source="shopify_graphql"))
        db.add(models.VariantDailyMetric(
            store_id=store.id, channel_id=channel.id, external_product_id=PRODUCT,
            external_variant_id=variant, variant_title="Standard", metric_date=day,
            units=units, revenue=revenue, refunded_units=0, refunded_revenue=0,
            currency=currency, source="shopify_graphql"))
    db.flush()


def _cost(db, store, *, amount="20.00", on=None, variant=VARIANT, currency="USD",
          source=product_costs.SHOPIFY):
    """What a unit cost. Without one there is no margin, and the measurement
    says so rather than treating the absence as zero."""
    return product_costs.record(
        db, store_id=store.id, product_id=PRODUCT, variant_id=variant,
        amount=Decimal(amount), currency=currency,
        effective_from=on or dt.date(2026, 1, 1), source=source,
        verification=product_costs.CONFIRMED)


def _ready_case(db, *, applied_at, now, units=1, revenue=60.0, cost="20.00",
                variant=VARIANT, currency="USD", **over):
    """A witnessed restock, a full window of sales, and a sync that read them.

    Returns ids, not objects: the tasks under test close and roll back the
    session these were made in.
    """
    store = _store(db)
    channel = _channel(db, store, synced_at=now,
                       synced_from=(applied_at - DAY).date())
    row = _applied_restock(db, store, applied_at=applied_at, **over)
    window = commerce_measurement.window_for(
        applied_at, measurement_days=row.measurement_days, now=now)
    _sales(db, store, channel, start=window.first, days=row.measurement_days,
           units=units, revenue=revenue, variant=variant, currency=currency)
    if cost is not None:
        _cost(db, store, amount=cost, on=applied_at.date(), variant=variant,
              currency=currency)
    db.commit()
    return store.id, channel.id, row.id


# --- the window, to the day -------------------------------------------------

def test_the_day_before_the_window_closes_nothing_is_measured():
    """Thirteen full days is not fourteen, and rounding up here would be the
    product quietly shortening its own promise."""
    applied = dt.datetime(2026, 9, 8, 12, 0, tzinfo=dt.timezone.utc)
    now = applied + dt.timedelta(days=14)      # days 1..13 are over
    window = commerce_measurement.window_for(applied, measurement_days=14, now=now)
    assert window.complete is False
    assert window.observed_days == 13
    assert window.days_remaining == 1


def test_on_the_day_of_the_change_the_whole_window_is_still_to_come():
    """Found against the real action: a 14-day window reported 15 days left.

    Nothing had elapsed, so the gap was measured from the day before the window
    opened — which is the day of the restock itself, and it belongs to neither
    side."""
    applied = dt.datetime(2026, 9, 8, 13, 59, tzinfo=dt.timezone.utc)
    window = commerce_measurement.window_for(
        applied, measurement_days=14, now=applied + dt.timedelta(hours=1))
    assert window.observed_days == 0
    assert window.days_remaining == 14
    assert window.complete is False


def test_every_day_of_the_window_is_accounted_for_exactly_once():
    """observed + remaining is the window, on every day of it."""
    applied = dt.datetime(2026, 9, 8, 12, 0, tzinfo=dt.timezone.utc)
    for offset in range(0, 16):
        window = commerce_measurement.window_for(
            applied, measurement_days=14, now=applied + dt.timedelta(days=offset))
        assert window.observed_days + window.days_remaining == 14, offset


def test_exactly_fourteen_full_days_later_it_is_measurable():
    applied = dt.datetime(2026, 9, 8, 12, 0, tzinfo=dt.timezone.utc)
    now = applied + dt.timedelta(days=15)
    window = commerce_measurement.window_for(applied, measurement_days=14, now=now)
    assert window.complete is True
    assert window.observed_days == 14
    assert window.days_remaining == 0


def test_the_day_of_the_change_belongs_to_neither_side():
    applied = dt.datetime(2026, 9, 8, 23, 59, tzinfo=dt.timezone.utc)
    window = commerce_measurement.window_for(
        applied, measurement_days=14, now=applied + dt.timedelta(days=20))
    assert window.first == dt.date(2026, 9, 9)
    assert window.last == dt.date(2026, 9, 22)


@pytest.mark.parametrize("hour", [0, 1, 12, 23])
def test_the_hour_of_day_never_shortens_the_window(hour):
    """A restock confirmed at one minute to midnight gets the same fourteen days
    as one confirmed at noon. Dates are computed in UTC on both sides; a local
    clock would hand somebody a thirteen-day window."""
    applied = dt.datetime(2026, 9, 8, hour, 30, tzinfo=dt.timezone.utc)
    now = dt.datetime(2026, 9, 23, 0, 5, tzinfo=dt.timezone.utc)
    window = commerce_measurement.window_for(applied, measurement_days=14, now=now)
    assert window.first == dt.date(2026, 9, 9) and window.last == dt.date(2026, 9, 22)
    assert window.complete is True


def test_a_naive_timestamp_is_read_as_utc_rather_than_local():
    """SQLite hands back timestamps without a timezone. Guessing local here would
    move somebody's window by a day depending on where the server is."""
    aware = dt.datetime(2026, 9, 8, 12, 0, tzinfo=dt.timezone.utc)
    naive = aware.replace(tzinfo=None)
    now = dt.datetime(2026, 9, 23, 12, 0, tzinfo=dt.timezone.utc)
    assert (commerce_measurement.window_for(naive, measurement_days=14, now=now)
            == commerce_measurement.window_for(aware, measurement_days=14, now=now))


# --- finding what is due ----------------------------------------------------

def test_an_open_window_is_not_due(db):
    applied = dt.datetime(2026, 9, 8, tzinfo=dt.timezone.utc)
    now = applied + dt.timedelta(days=10)
    store_id, _, _ = _ready_case(db, applied_at=applied, now=now)
    assert commerce_measurement.due_actions(db, store_id=store_id, now=now) == []


def test_a_closed_window_is_due(db):
    applied = dt.datetime(2026, 9, 8, tzinfo=dt.timezone.utc)
    now = applied + dt.timedelta(days=15)
    store_id, _, action_id = _ready_case(db, applied_at=applied, now=now)
    assert [r.id for r in commerce_measurement.due_actions(
        db, store_id=store_id, now=now)] == [action_id]


def test_an_already_priced_action_is_not_due_again(db):
    applied = dt.datetime(2026, 9, 8, tzinfo=dt.timezone.utc)
    now = applied + dt.timedelta(days=15)
    store_id, _, action_id = _ready_case(db, applied_at=applied, now=now)
    row = _action(db, action_id)
    row.measured_at = now
    db.add(row); db.commit()
    assert commerce_measurement.due_actions(db, store_id=store_id, now=now) == []


# --- the automatic run ------------------------------------------------------

def test_the_daily_run_prices_a_closed_window_with_nobody_watching(bound):
    """The gap this closes: an action could sit `applied` for ever, because the
    only thing that ever measured one was a person pressing a button."""
    applied = dt.datetime(2026, 9, 8, tzinfo=dt.timezone.utc)
    now = applied + dt.timedelta(days=15)
    _, _, action_id = _ready_case(bound, applied_at=applied, now=now)

    assert tasks.measure_due_actions(now=now)["measured"] == 1

    row = _action(bound, action_id)
    assert row.status == "measured" and row.measured_at is not None
    assert row.impact["observed_units"] == 14
    assert row.impact["attributable_units"] == 10      # 14 sold, 4 already there
    # 60.00 a unit against a 20.00 cost, on the ten units the shelf could not
    # have covered.
    assert row.impact["net"] == 400.0


def test_the_daily_run_measures_nothing_a_day_early(bound):
    applied = dt.datetime(2026, 9, 8, tzinfo=dt.timezone.utc)
    now = applied + dt.timedelta(days=14)
    _, _, action_id = _ready_case(bound, applied_at=applied, now=now)

    assert tasks.measure_due_actions(now=now)["measured"] == 0
    row = _action(bound, action_id)
    assert row.measured_at is None and row.impact is None


def test_running_the_job_twice_does_not_price_it_twice(bound):
    applied = dt.datetime(2026, 9, 8, tzinfo=dt.timezone.utc)
    now = applied + dt.timedelta(days=15)
    _, _, action_id = _ready_case(bound, applied_at=applied, now=now)

    first = tasks.measure_due_actions(now=now)
    row = _action(bound, action_id)
    measured_at, impact = row.measured_at, dict(row.impact)

    second = tasks.measure_due_actions(now=now + DAY)
    row = _action(bound, action_id)
    assert first["measured"] == 1 and second["measured"] == 0
    assert row.measured_at == measured_at and row.impact == impact


def test_one_broken_tenant_does_not_stop_the_others(bound, monkeypatch):
    """A batch job that dies on its first bad row is a batch job that stops
    working the day one customer's data is odd."""
    applied = dt.datetime(2026, 9, 8, tzinfo=dt.timezone.utc)
    now = applied + dt.timedelta(days=15)
    broken_store, _, broken_id = _ready_case(bound, applied_at=applied, now=now)
    _, _, good_id = _ready_case(bound, applied_at=applied, now=now)

    real_measure = commerce_measurement.measure

    def explode(db, row, **kw):
        if row.store_id == broken_store:
            raise RuntimeError("something specific to this tenant")
        return real_measure(db, row, **kw)

    monkeypatch.setattr(commerce_measurement, "measure", explode)
    result = tasks.measure_due_actions(now=now)

    assert result["failed"] == 1 and result["measured"] == 1
    assert _action(bound, good_id).measured_at is not None
    assert _action(bound, broken_id).measured_at is None


def test_one_tenant_is_never_measured_from_another_tenants_sales(bound):
    """Both shops sold the same product id. The measurement must read one."""
    applied = dt.datetime(2026, 9, 8, tzinfo=dt.timezone.utc)
    now = applied + dt.timedelta(days=15)
    _, _, mine = _ready_case(bound, applied_at=applied, now=now)
    # Their shop sells ten times as much of exactly the same product.
    _ready_case(bound, applied_at=applied, now=now, units=10, revenue=600.0)

    tasks.measure_due_actions(now=now)
    assert _action(bound, mine).impact["observed_units"] == 14, \
        "their sales leaked into mine"


# --- incomplete data --------------------------------------------------------

def _uncovered_case(db, *, applied_at, synced_at=None, synced_from=None,
                    status="connected"):
    store = _store(db)
    channel = _channel(db, store, synced_at=synced_at, synced_from=synced_from,
                       status=status)
    row = _applied_restock(db, store, applied_at=applied_at)
    db.commit()
    return store.id, channel.id, row.id


def test_a_window_nothing_read_is_left_waiting_rather_than_priced_at_zero(bound):
    """The days passed while nothing was syncing. `product_daily_metrics` is
    empty for them, and empty looks exactly like no sales."""
    applied = dt.datetime(2026, 9, 8, tzinfo=dt.timezone.utc)
    now = applied + dt.timedelta(days=15)
    _, _, action_id = _uncovered_case(bound, applied_at=applied)

    result = tasks.measure_due_actions(now=now)
    assert result["measured"] == 0 and result["waiting"] == 1
    assert commerce_measurement.AWAITING_DATA in result["reasons"]

    row = _action(bound, action_id)
    assert row.measured_at is None and row.status == "applied"


def test_a_sync_that_stopped_before_the_window_closed_is_not_enough(bound):
    applied = dt.datetime(2026, 9, 8, tzinfo=dt.timezone.utc)
    now = applied + dt.timedelta(days=15)
    # Last read a week ago: the final days of the window were never fetched.
    _, _, action_id = _uncovered_case(
        bound, applied_at=applied, synced_at=now - dt.timedelta(days=7),
        synced_from=(applied - DAY).date())

    assert tasks.measure_due_actions(now=now)["measured"] == 0
    assert _action(bound, action_id).measured_at is None


def test_a_sync_that_did_not_reach_back_to_the_start_is_not_enough(bound):
    """A seven-day read cannot certify a fortnight."""
    applied = dt.datetime(2026, 9, 8, tzinfo=dt.timezone.utc)
    now = applied + dt.timedelta(days=15)
    _, _, action_id = _uncovered_case(bound, applied_at=applied, synced_at=now,
                                      synced_from=dt.date(2026, 9, 20))

    assert tasks.measure_due_actions(now=now)["measured"] == 0
    assert _action(bound, action_id).measured_at is None


def test_the_measurement_is_retried_once_the_data_arrives(bound):
    """Waiting is not failing. The action stays due, and the day a sync finally
    covers the window it is priced from the real days."""
    applied = dt.datetime(2026, 9, 8, tzinfo=dt.timezone.utc)
    now = applied + dt.timedelta(days=15)
    store_id, channel_id, action_id = _uncovered_case(bound, applied_at=applied)

    assert tasks.measure_due_actions(now=now)["measured"] == 0

    window = commerce_measurement.window_for(applied, measurement_days=14, now=now)
    store = bound.get(models.Store, store_id)
    channel = bound.get(models.ChannelConnection, channel_id)
    _sales(bound, store, channel, start=window.first, days=14)
    channel.synced_at = now + DAY
    channel.settings = {"synced_from": (applied - DAY).date().isoformat()}
    bound.add(channel)
    _cost(bound, store, amount="20.00", on=applied.date())
    bound.commit()

    assert tasks.measure_due_actions(now=now + DAY)["measured"] == 1
    assert _action(bound, action_id).impact["observed_units"] == 14


def test_a_revoked_token_does_not_turn_into_a_bad_result(bound):
    """The credential broke after the days were already read and stored. Losing
    a token must not retroactively price somebody's restock as a failure."""
    applied = dt.datetime(2026, 9, 8, tzinfo=dt.timezone.utc)
    now = applied + dt.timedelta(days=15)
    _, channel_id, action_id = _ready_case(bound, applied_at=applied, now=now)
    channel = bound.get(models.ChannelConnection, channel_id)
    channel.status = "revoked"
    bound.add(channel); bound.commit()

    assert tasks.measure_due_actions(now=now)["measured"] == 1
    assert _action(bound, action_id).impact["attributable_units"] == 10, \
        "the stored days still count"


def test_a_revoked_token_before_any_data_leaves_it_waiting(bound):
    applied = dt.datetime(2026, 9, 8, tzinfo=dt.timezone.utc)
    now = applied + dt.timedelta(days=15)
    _, _, action_id = _uncovered_case(bound, applied_at=applied, status="revoked")

    assert tasks.measure_due_actions(now=now)["waiting"] == 1
    row = _action(bound, action_id)
    assert row.measured_at is None and row.impact is None


# --- what the payroll will accept -------------------------------------------

def test_a_priced_restock_reaches_the_payroll(bound):
    """The point of all of it. A witnessed change, a closed window, days that
    were read, units beyond the old shelf and a cost for every one of them."""
    applied = dt.datetime(2026, 9, 8, tzinfo=dt.timezone.utc)
    now = applied + dt.timedelta(days=15)
    _, _, action_id = _ready_case(bound, applied_at=applied, now=now)
    tasks.measure_due_actions(now=now)
    row = _action(bound, action_id)

    assert row.evidence_mode == "real"
    payroll = actions_engine.build_payroll(
        [{"status": row.status, "impact": row.impact}], operator_cost=0.0)
    assert payroll.settled_impact == 400.0


def test_the_headline_is_margin_and_never_revenue(bound):
    """Revenue with a profit's name on it is the failure this whole layer
    exists to prevent."""
    applied = dt.datetime(2026, 9, 8, tzinfo=dt.timezone.utc)
    now = applied + dt.timedelta(days=15)
    _, _, action_id = _ready_case(bound, applied_at=applied, now=now)
    tasks.measure_due_actions(now=now)
    impact = _action(bound, action_id).impact
    assert impact["attributable_revenue"] == 600.0
    assert impact["attributable_cost"] == 200.0
    assert impact["net"] == 400.0


def test_a_window_with_no_cost_on_record_is_not_priced_at_all(bound):
    """Not zero, and not the whole sale price called profit. It waits, and it
    says what it is waiting for."""
    applied = dt.datetime(2026, 9, 8, tzinfo=dt.timezone.utc)
    now = applied + dt.timedelta(days=15)
    _, _, action_id = _ready_case(bound, applied_at=applied, now=now, cost=None)

    result = tasks.measure_due_actions(now=now)
    assert result["measured"] == 0 and result["waiting"] == 1
    assert commerce_measurement.AWAITING_COST in result["reasons"]

    row = _action(bound, action_id)
    assert row.measured_at is None and row.impact is None


def test_the_cost_arriving_later_lets_the_measurement_finish_by_itself(bound):
    """No second confirmation, no re-application, no touching the baseline: the
    action was already due, and the next run simply finds it priceable."""
    applied = dt.datetime(2026, 9, 8, tzinfo=dt.timezone.utc)
    now = applied + dt.timedelta(days=15)
    store_id, _, action_id = _ready_case(bound, applied_at=applied, now=now,
                                         cost=None)
    baseline_before = dict(_action(bound, action_id).baseline)
    assert tasks.measure_due_actions(now=now)["measured"] == 0

    store = bound.get(models.Store, store_id)
    _cost(bound, store, amount="20.00", on=applied.date())
    bound.commit()

    assert tasks.measure_due_actions(now=now + DAY)["measured"] == 1
    row = _action(bound, action_id)
    assert row.evidence_mode == "real" and row.impact["net"] == 400.0
    assert row.baseline == baseline_before, "the before-picture must not move"


def test_a_cost_in_the_wrong_currency_blocks_rather_than_converts(bound):
    """No FX source exists in this project, and inventing a rate would be the
    one kind of number this product refuses to produce."""
    applied = dt.datetime(2026, 9, 8, tzinfo=dt.timezone.utc)
    now = applied + dt.timedelta(days=15)
    store_id, _, action_id = _ready_case(bound, applied_at=applied, now=now,
                                         cost=None)
    store = bound.get(models.Store, store_id)
    _cost(bound, store, amount="18.00", on=applied.date(), currency="EUR")
    bound.commit()

    result = tasks.measure_due_actions(now=now)
    assert result["measured"] == 0
    assert commerce_measurement.AWAITING_COST in result["reasons"]
    assert _action(bound, action_id).measured_at is None


def test_a_second_variant_without_a_cost_blocks_the_whole_result(bound):
    """Measuring only the priced half would raise the average margin of what
    remains and call the difference proven."""
    applied = dt.datetime(2026, 9, 8, tzinfo=dt.timezone.utc)
    now = applied + dt.timedelta(days=15)
    store_id, channel_id, action_id = _ready_case(bound, applied_at=applied, now=now)
    store = bound.get(models.Store, store_id)
    channel = bound.get(models.ChannelConnection, channel_id)
    window = commerce_measurement.window_for(applied, measurement_days=14, now=now)
    _sales(bound, store, channel, start=window.first, days=1, units=3,
           revenue=300.0, variant="gid://shopify/ProductVariant/99",
           product_row=False)
    bound.commit()

    result = tasks.measure_due_actions(now=now)
    assert result["measured"] == 0
    assert commerce_measurement.AWAITING_COST in result["reasons"]


def test_a_refunded_unit_is_not_a_sold_one(bound):
    """It left the shelf and came back. Counting it would price a return as a
    result."""
    applied = dt.datetime(2026, 9, 8, tzinfo=dt.timezone.utc)
    now = applied + dt.timedelta(days=15)
    store_id, channel_id, action_id = _ready_case(bound, applied_at=applied, now=now)
    window = commerce_measurement.window_for(applied, measurement_days=14, now=now)
    first_day = bound.scalar(select(models.VariantDailyMetric).where(
        models.VariantDailyMetric.metric_date == window.first))
    first_day.refunded_units = 1
    first_day.refunded_revenue = 60.0
    bound.add(first_day); bound.commit()

    tasks.measure_due_actions(now=now)
    impact = _action(bound, action_id).impact
    assert impact["observed_units"] == 13, "the refunded unit was counted as sold"
    assert impact["attributable_units"] == 9


def test_an_unwitnessed_change_stays_out_however_well_it_is_priced(bound):
    applied = dt.datetime(2026, 9, 8, tzinfo=dt.timezone.utc)
    now = applied + dt.timedelta(days=15)
    _, _, action_id = _ready_case(bound, applied_at=applied, now=now, witnessed=False)
    tasks.measure_due_actions(now=now)
    row = _action(bound, action_id)
    assert row.evidence_mode == "unverified"
    assert "read back from Shopify" in row.impact["evidence_reason"]


def test_sales_the_old_shelf_could_have_covered_finish_at_zero(bound):
    """A completed answer of nothing. Not a positive proven effect, and not a
    reason to wait for ever."""
    applied = dt.datetime(2026, 9, 8, tzinfo=dt.timezone.utc)
    now = applied + dt.timedelta(days=15)
    _, _, action_id = _ready_case(bound, applied_at=applied, now=now,
                                  units=0, on_hand=100)
    tasks.measure_due_actions(now=now)
    row = _action(bound, action_id)
    assert row.status == "measured"
    assert row.impact["attributable_units"] == 0 and row.impact["net"] == 0.0
    assert row.evidence_mode == "unverified"


# --- the scheduler around it ------------------------------------------------

def test_two_schedulers_do_not_both_take_the_measurement_run(db):
    key = scheduler.run_key_for()
    assert scheduler.claim(db, job=scheduler.MEASUREMENT, run_key=key) is not None
    db.commit()
    assert scheduler.claim(db, job=scheduler.MEASUREMENT, run_key=key) is None


def test_each_job_keeps_its_own_day(db):
    """One stage having run is not the other stage having run, and a shared key
    would let a working sync hide a measurement that stopped."""
    key = scheduler.run_key_for()
    for job in scheduler.JOBS:
        run = scheduler.claim(db, job=job, run_key=key)
        assert run is not None, job
        db.commit(); scheduler.finish(db, run)
    assert {r.job for r in db.scalars(select(models.SchedulerRun))} == set(scheduler.JOBS)


def test_each_job_keeps_its_own_freshness(db):
    """A sync that ran does not make a measurement that stopped look fresh."""
    run = scheduler.claim(db, job=scheduler.SHOPIFY_SYNC, run_key=scheduler.run_key_for())
    db.commit(); scheduler.finish(db, run)
    assert scheduler.is_stale(db, job=scheduler.SHOPIFY_SYNC) is False
    assert scheduler.hours_since_success(db, job=scheduler.MEASUREMENT) is None


def test_a_scheduler_restart_does_not_lose_an_unmeasured_action(bound):
    """Nothing about being due lives in memory or in Redis. The action is due
    because of what its row says, so a process that died mid-run finds it again
    on the next firing."""
    applied = dt.datetime(2026, 9, 8, tzinfo=dt.timezone.utc)
    now = applied + dt.timedelta(days=15)
    _, _, action_id = _ready_case(bound, applied_at=applied, now=now)

    # The run claimed the day and the container died before doing anything. The
    # claim's own staleness is measured against the real clock, not the window's.
    key = scheduler.run_key_for(now)
    run = scheduler.claim(bound, job=scheduler.MEASUREMENT, run_key=key)
    bound.commit()
    run.started_at = (dt.datetime.now(dt.timezone.utc)
                      - dt.timedelta(hours=scheduler.STALE_RUN_HOURS + 1))
    bound.add(run); bound.commit()

    assert scheduler.claim(bound, job=scheduler.MEASUREMENT, run_key=key) is not None
    assert tasks.measure_due_actions(now=now)["measured"] == 1


def test_a_dry_run_prices_nothing(bound):
    applied = dt.datetime(2026, 9, 8, tzinfo=dt.timezone.utc)
    now = applied + dt.timedelta(days=15)
    _, _, action_id = _ready_case(bound, applied_at=applied, now=now)

    result = tasks.measure_due_actions(now=now, dry_run=True)
    assert result["measured"] == 1                        # would have
    assert _action(bound, action_id).measured_at is None   # did not
