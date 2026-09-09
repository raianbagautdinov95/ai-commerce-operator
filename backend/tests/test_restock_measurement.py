"""What a restock may be said to have earned.

Two rules, and the tests are mostly about their refusals.

The first is the counterfactual, and it needs no control group: a shop holding
one unit could not have sold thirty. Units sold beyond the stock that already
existed are the ones the restock made possible, and they are the only ones
claimed. The before-and-after revenue rate is never used — sales did rise after
the shelf was refilled, and they also rose after that Tuesday.

The second is that a margin needs a cost. One variant with no cost on record
blocks the whole result rather than being quietly dropped, because dropping it
would raise the average margin of everything that remains.
"""
import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import actions_engine

D = Decimal


def _sold(units, revenue, cost, *, variant="v1", currency="USD",
          cost_currency="USD", title=None):
    return actions_engine.SoldUnits(
        variant_id=variant, units=units, revenue=D(str(revenue)),
        unit_cost=None if cost is None else D(str(cost)),
        currency=currency, cost_currency=None if cost is None else cost_currency,
        variant_title=title)


def _measure(on_hand, sales, *, days=14, requested=14):
    return actions_engine.measure_restock(
        {"on_hand": on_hand, "product_id": "gid://shopify/Product/1"},
        sales, days=days, requested_days=requested)


# --- the counterfactual -----------------------------------------------------

def test_only_the_units_the_old_stock_could_not_have_covered_are_claimed():
    """Five sold, one was already there: four needed the restock. Each earned
    100.00 and cost 60.00."""
    result = _measure(1, [_sold(5, "500.00", "60.00")])
    assert result.blocked is None
    assert result.impact.observed_units == 5
    assert result.impact.attributable_units == 4
    assert result.impact.attributable_margin == D("160.00")


def test_sales_the_shelf_could_already_have_covered_prove_nothing():
    """A finished answer, and the answer is nothing. Not a failure and not a
    reason to keep waiting."""
    result = _measure(10, [_sold(6, "600.00", "60.00")])
    assert result.blocked is None
    assert result.impact.attributable_units == 0
    assert result.impact.attributable_margin == D("0")
    assert "could have covered" in result.impact.basis


def test_the_before_and_after_rate_is_never_used():
    """Identical sales price identically however busy or quiet the shop was
    beforehand. The baseline revenue is not an input."""
    quiet = actions_engine.measure_restock(
        {"on_hand": 1, "days": 10, "revenue": 0.0}, [_sold(5, "500.00", "60.00")],
        days=14)
    busy = actions_engine.measure_restock(
        {"on_hand": 1, "days": 10, "revenue": 9999.0}, [_sold(5, "500.00", "60.00")],
        days=14)
    assert quiet.impact.attributable_margin == busy.impact.attributable_margin


def test_oversold_stock_counts_as_an_empty_shelf():
    """Shopify reports oversold as a negative. Nothing was there to sell."""
    result = _measure(-3, [_sold(4, "400.00", "60.00")])
    assert result.impact.attributable_units == 4


def test_an_empty_shelf_makes_every_sale_attributable():
    result = _measure(0, [_sold(7, "700.00", "60.00")])
    assert result.impact.attributable_units == 7
    assert result.impact.attributable_margin == D("280.00")


# --- which units the old shelf is assumed to have covered -------------------

def test_the_units_assumed_to_have_sold_anyway_are_the_most_profitable_ones():
    """The allocation is a choice, and it is made against ourselves: what is
    claimed is the cheapest explanation of the same sales.

    Small earns 40 a unit, Large earns 50. Two were already on the shelf, so the
    two Large units are the ones assumed to have sold regardless.
    """
    small = _sold(4, "400.00", "60.00", variant="small", title="Small")
    large = _sold(4, "800.00", "150.00", variant="large", title="Large")
    result = _measure(2, [small, large])
    assert result.impact.attributable_units == 6
    assert result.impact.attributable_margin == D("260.00")   # 2x50 + 4x40


def test_the_allocation_does_not_depend_on_the_order_of_the_input():
    small = _sold(4, "400.00", "60.00", variant="small")
    large = _sold(4, "800.00", "150.00", variant="large")
    one = _measure(2, [small, large]).impact.attributable_margin
    other = _measure(2, [large, small]).impact.attributable_margin
    assert one == other == D("260.00")


def test_a_cost_that_changed_mid_window_prices_each_day_as_it_was():
    """Two groups for one variant, because the cost moved. Nothing averages
    them: the unit that sold on the dearer day cost the dearer amount."""
    cheap = _sold(3, "300.00", "60.00", variant="v1")
    dear = _sold(3, "300.00", "80.00", variant="v1")
    result = _measure(0, [cheap, dear])
    assert result.impact.attributable_units == 6
    assert result.impact.attributable_margin == D("180.00")   # 3x40 + 3x20


# --- what it refuses --------------------------------------------------------

def test_one_variant_without_a_cost_blocks_the_whole_result():
    """Dropping it would raise the average margin of everything left."""
    priced = _sold(4, "400.00", "60.00", variant="small", title="Small")
    unpriced = _sold(4, "800.00", None, variant="large", title="Large")
    result = _measure(1, [priced, unpriced])
    assert result.blocked == actions_engine.COST_UNKNOWN
    assert result.impact is None
    assert "Large" in result.reason


def test_a_missing_cost_does_not_block_a_window_that_sold_nothing_extra():
    """Nothing is being claimed, so nothing needs pricing. The answer is zero
    either way, and leaving it unmeasured would be waiting for a number that
    could not change it."""
    result = _measure(10, [_sold(4, "400.00", None)])
    assert result.blocked is None and result.impact.attributable_units == 0


def test_two_sale_currencies_are_never_added_together():
    result = _measure(0, [_sold(2, "200.00", "60.00"),
                          _sold(2, "180.00", "50.00", variant="v2",
                                currency="EUR", cost_currency="EUR")])
    assert result.blocked == actions_engine.CURRENCY_MISMATCH


def test_a_cost_in_another_currency_than_the_sale_is_refused():
    """Subtracting one from the other would invent an exchange rate."""
    result = _measure(0, [_sold(2, "200.00", "60.00", cost_currency="EUR")])
    assert result.blocked == actions_engine.CURRENCY_MISMATCH
    assert "EUR" in result.reason


def test_a_window_too_short_to_prove_anything_is_refused():
    result = _measure(1, [_sold(9, "900.00", "60.00")],
                      days=actions_engine.MIN_MEASURABLE_DAYS - 1)
    assert result.blocked == actions_engine.WINDOW_TOO_SHORT


def test_a_baseline_with_no_stock_level_cannot_be_compared():
    result = actions_engine.measure_restock(
        {"product_id": "gid://shopify/Product/1"}, [_sold(5, "500.00", "60.00")],
        days=14)
    assert result.blocked == actions_engine.NOTHING_TO_COMPARE


def test_nothing_to_measure_returns_a_reason_rather_than_a_number():
    result = actions_engine.measure_restock(None, [_sold(1, "1.00", "0.50")], days=14)
    assert result.impact is None and result.blocked == actions_engine.NOTHING_TO_COMPARE


def test_a_window_with_no_sales_does_not_divide_by_zero():
    result = _measure(1, [])
    assert result.impact.attributable_units == 0
    assert result.impact.observed_units == 0


# --- money ------------------------------------------------------------------

def test_the_arithmetic_is_decimal_from_end_to_end():
    """Three units at 0.30, costing 0.10 each: 0.20 of margin apiece.

    In binary floating point 0.1 + 0.2 is not 0.3, and a margin built out of
    those disagrees with itself once the sales add up. This is the whole reason
    the module never sees a float.
    """
    result = _measure(0, [_sold(3, "0.90", "0.10")])
    assert result.impact.attributable_margin == D("0.60")
    assert isinstance(result.impact.attributable_margin, Decimal)
    assert 0.1 + 0.2 != 0.3, "the reason this test exists"


def test_a_fraction_of_a_penny_is_rounded_against_the_claim():
    """Never up. An eighth of a penny in our favour on every claim is still a
    claim nobody measured."""
    result = _measure(0, [_sold(3, "1.00", "0.00")])
    # 1.00 over three units is 0.3333… each; three of them is 0.9999…
    assert result.impact.attributable_margin == D("0.99")


def test_zero_cost_is_a_cost_and_not_a_refusal():
    """A shop may genuinely have paid nothing. That is a measurable margin."""
    result = _measure(0, [_sold(2, "200.00", "0")])
    assert result.blocked is None
    assert result.impact.attributable_margin == D("200.00")


# --- what it says about itself ----------------------------------------------

def test_an_incomplete_window_says_so():
    result = _measure(1, [_sold(5, "500.00", "60.00")], days=5, requested=14)
    assert result.impact.provisional is True
    assert "not yet complete" in result.impact.basis


def test_a_complete_window_is_not_provisional():
    result = _measure(1, [_sold(5, "500.00", "60.00")], days=14, requested=14)
    assert result.impact.provisional is False


def test_the_basis_names_what_the_margin_does_not_include():
    """Gross margin on the incremental units. Shipping and payment fees are per
    order rather than per unit, and allocating them would be an assumption."""
    basis = _measure(1, [_sold(5, "500.00", "60.00")]).impact.basis
    assert "shipping" in basis and "fees" in basis
    assert "after discounts, tax and refunds" in basis


def test_the_payroll_reads_the_margin_and_not_the_revenue():
    """`build_payroll` adds up `net`. It has to be the margin, or the headline
    becomes revenue with a profit's name on it."""
    impact = _measure(1, [_sold(5, "500.00", "60.00")]).impact.to_dict()
    assert impact["net"] == 160.0
    assert impact["attributable_revenue"] == 400.0
