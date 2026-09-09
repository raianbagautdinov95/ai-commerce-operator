"""What a unit cost, where the figure came from, and when it started being true.

The failure this exists to prevent is a quiet one: a cost overwritten in place.
Every result already priced would silently restate itself, and the seller would
have no way of noticing. So nothing is updated — a new value is a new row with
its own start date, and the lookup asks a question with a date in it.

The other failure is treating an absent cost as zero. A cost nobody supplied and
a cost of nothing are opposite claims, and only one of them can be measured; the
difference between them is the difference between a proven margin and the whole
sale price called profit.
"""
import datetime as dt
import os
import sys
import uuid
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import product_costs
from app.db import models
from app.db.models import Base

PRODUCT = "gid://shopify/Product/1"
SMALL = "gid://shopify/ProductVariant/10"
LARGE = "gid://shopify/ProductVariant/20"
JAN = dt.date(2026, 1, 1)


@pytest.fixture
def db():
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    yield session
    session.close()


@pytest.fixture
def store(db):
    user = models.User(email=f"{uuid.uuid4().hex[:8]}@example.test")
    db.add(user); db.flush()
    row = models.Store(user_id=user.id, marketplace="US")
    db.add(row); db.commit()
    return row


def _record(db, store, *, amount, on, variant=SMALL, source=product_costs.SHOPIFY,
            verification=product_costs.CONFIRMED, currency="USD"):
    return product_costs.record(
        db, store_id=store.id, product_id=PRODUCT, variant_id=variant,
        amount=Decimal(str(amount)), currency=currency, effective_from=on,
        source=source, verification=verification, commit=True)


# --- provenance -------------------------------------------------------------

def test_a_cost_read_from_shopify_says_so(db, store):
    row = _record(db, store, amount="120.00", on=JAN)
    assert row.source == product_costs.SHOPIFY
    assert row.verification == product_costs.CONFIRMED


def test_a_cost_a_person_typed_is_marked_as_reported(db, store):
    row = _record(db, store, amount="120.00", on=JAN, source=product_costs.MANUAL,
                  verification=product_costs.REPORTED)
    assert row.source == product_costs.MANUAL
    assert row.verification == product_costs.REPORTED


def test_a_seller_overrides_what_shopify_holds(db, store):
    """Both effective the same day, and the seller typed theirs first.

    Written in this order on purpose: if the newest observation simply won, the
    sync that ran afterwards would silently undo what the seller had entered,
    and they would find their own figure gone with nothing to show why."""
    _record(db, store, amount="90.00", on=JAN, source=product_costs.MANUAL,
            verification=product_costs.REPORTED)
    _record(db, store, amount="120.00", on=JAN)
    cost = product_costs.cost_on(db, store_id=store.id, product_id=PRODUCT,
                                 variant_id=SMALL, on=JAN)
    assert cost.amount == Decimal("90.00") and cost.source == product_costs.MANUAL


# --- unknown is not zero ----------------------------------------------------

def test_no_cost_at_all_is_unknown(db, store):
    assert product_costs.cost_on(db, store_id=store.id, product_id=PRODUCT,
                                 variant_id=SMALL, on=JAN) is None


def test_a_cost_that_starts_later_is_unknown_before_it_starts(db, store):
    _record(db, store, amount="120.00", on=dt.date(2026, 2, 1))
    assert product_costs.cost_on(db, store_id=store.id, product_id=PRODUCT,
                                 variant_id=SMALL, on=JAN) is None


def test_zero_is_allowed_but_only_when_somebody_says_so(db, store):
    """A shop may genuinely have paid nothing. That is a value; an absent field
    is not."""
    _record(db, store, amount="0", on=JAN)
    cost = product_costs.cost_on(db, store_id=store.id, product_id=PRODUCT,
                                 variant_id=SMALL, on=JAN)
    assert cost is not None and cost.amount == Decimal("0")


def test_a_negative_cost_is_refused(db, store):
    assert _record(db, store, amount="-5", on=JAN) is None
    assert db.scalar(select(models.ProductCost)) is None


def test_an_unattributable_variant_never_finds_a_cost(db, store):
    """A line item that arrived without a variant. Pricing it from a
    neighbour's cost would be a guess wearing a measurement's clothes."""
    _record(db, store, amount="120.00", on=JAN, variant="")
    assert product_costs.cost_on(
        db, store_id=store.id, product_id=PRODUCT,
        variant_id=product_costs.unknown_variant_key(PRODUCT), on=JAN) is None


# --- history ----------------------------------------------------------------

def test_a_new_cost_does_not_erase_the_old_one(db, store):
    _record(db, store, amount="100.00", on=JAN)
    _record(db, store, amount="130.00", on=dt.date(2026, 3, 1))
    rows = product_costs.history(db, store_id=store.id)
    assert len(rows) == 2


def test_a_sale_is_priced_by_the_cost_in_force_that_day(db, store):
    _record(db, store, amount="100.00", on=JAN)
    _record(db, store, amount="130.00", on=dt.date(2026, 3, 1))
    before = product_costs.cost_on(db, store_id=store.id, product_id=PRODUCT,
                                   variant_id=SMALL, on=dt.date(2026, 2, 28))
    after = product_costs.cost_on(db, store_id=store.id, product_id=PRODUCT,
                                  variant_id=SMALL, on=dt.date(2026, 3, 2))
    assert before.amount == Decimal("100.00")
    assert after.amount == Decimal("130.00")


def test_the_same_value_read_again_does_not_add_a_row(db, store):
    """The sync runs daily. A history with a row per day stops being readable
    as a history."""
    _record(db, store, amount="100.00", on=JAN)
    _record(db, store, amount="100.00", on=dt.date(2026, 1, 2))
    _record(db, store, amount="100.00", on=dt.date(2026, 1, 3))
    assert len(product_costs.history(db, store_id=store.id)) == 1


def test_a_correction_on_the_same_day_replaces_rather_than_stacks(db, store):
    """Nothing has been priced from today yet — measurement only reads days that
    are over — so a same-day correction loses nothing."""
    _record(db, store, amount="100.00", on=JAN)
    _record(db, store, amount="105.00", on=JAN)
    rows = product_costs.history(db, store_id=store.id)
    assert len(rows) == 1 and Decimal(str(rows[0].amount)) == Decimal("105.00")


def test_a_deleted_variant_keeps_the_cost_it_had(db, store):
    """The variant is gone from Shopify; the units it sold still happened."""
    _record(db, store, amount="100.00", on=JAN, variant=LARGE)
    cost = product_costs.cost_on(db, store_id=store.id, product_id=PRODUCT,
                                 variant_id=LARGE, on=dt.date(2026, 6, 1))
    assert cost.amount == Decimal("100.00")


# --- variants ---------------------------------------------------------------

def test_two_variants_keep_their_own_costs(db, store):
    _record(db, store, amount="100.00", on=JAN, variant=SMALL)
    _record(db, store, amount="180.00", on=JAN, variant=LARGE)
    small = product_costs.cost_on(db, store_id=store.id, product_id=PRODUCT,
                                  variant_id=SMALL, on=JAN)
    large = product_costs.cost_on(db, store_id=store.id, product_id=PRODUCT,
                                  variant_id=LARGE, on=JAN)
    assert (small.amount, large.amount) == (Decimal("100.00"), Decimal("180.00"))


def test_a_product_wide_cost_covers_a_variant_with_none_of_its_own(db, store):
    _record(db, store, amount="95.00", on=JAN, variant="")
    cost = product_costs.cost_on(db, store_id=store.id, product_id=PRODUCT,
                                 variant_id=LARGE, on=JAN)
    assert cost.amount == Decimal("95.00") and cost.exact_variant is False


def test_a_variants_own_cost_beats_the_product_wide_one(db, store):
    _record(db, store, amount="95.00", on=JAN, variant="")
    _record(db, store, amount="180.00", on=JAN, variant=LARGE)
    cost = product_costs.cost_on(db, store_id=store.id, product_id=PRODUCT,
                                 variant_id=LARGE, on=JAN)
    assert cost.amount == Decimal("180.00") and cost.exact_variant is True


def test_one_tenant_never_reads_anothers_costs(db, store):
    """What a shop pays for its stock is close to the most sensitive number
    in it."""
    other_user = models.User(email=f"{uuid.uuid4().hex[:8]}@example.test")
    db.add(other_user); db.flush()
    theirs = models.Store(user_id=other_user.id, marketplace="US")
    db.add(theirs); db.commit()
    _record(db, theirs, amount="1.00", on=JAN)

    assert product_costs.cost_on(db, store_id=store.id, product_id=PRODUCT,
                                 variant_id=SMALL, on=JAN) is None


# --- decimals ---------------------------------------------------------------

def test_money_stays_decimal_from_end_to_end(db, store):
    """A margin computed in binary floating point disagrees with itself at the
    fourth sale."""
    _record(db, store, amount="0.10", on=JAN, variant=SMALL)
    _record(db, store, amount="0.20", on=JAN, variant=LARGE)
    small = product_costs.cost_on(db, store_id=store.id, product_id=PRODUCT,
                                  variant_id=SMALL, on=JAN)
    large = product_costs.cost_on(db, store_id=store.id, product_id=PRODUCT,
                                  variant_id=LARGE, on=JAN)
    assert isinstance(small.amount, Decimal)
    assert small.amount + large.amount == Decimal("0.30")
    assert 0.1 + 0.2 != 0.3, "the reason this test exists"


def test_a_cost_with_four_places_is_not_rounded_at_rest(db, store):
    _record(db, store, amount="12.3456", on=JAN)
    cost = product_costs.cost_on(db, store_id=store.id, product_id=PRODUCT,
                                 variant_id=SMALL, on=JAN)
    assert cost.amount == Decimal("12.3456")


def test_nonsense_is_not_read_as_a_number(db, store):
    assert product_costs.to_decimal("about twelve") is None
    assert product_costs.to_decimal(None) is None
    assert product_costs.to_decimal("") is None
    assert product_costs.to_decimal("12.34") == Decimal("12.34")
