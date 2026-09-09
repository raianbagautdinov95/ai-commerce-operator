"""
Tests for the commerce engine.

The engine's job is as much refusal as detection. Most of these tests check that
it stays quiet — on a store too young to have a trend, on a ratio computed from
pocket change, on a figure it cannot honestly name. A finding that fires when it
should not costs a seller their attention; a number invented to accompany it
costs them their trust.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import commerce_engine as engine


def _day(date: str, *, revenue: float = 0.0, refunds: float = 0.0, orders: int = 0,
         units: int = 0, spend: float = 0.0, costs_complete: bool = False) -> dict:
    return {"date": date, "revenue": revenue, "refunds": refunds, "orders": orders,
            "units": units, "advertising_spend": spend, "costs_complete": costs_complete}


def _series(count: int, *, start_day: int = 1, **kwargs) -> list[dict]:
    """`count` consecutive days in September 2026, all alike."""
    return [_day(f"2026-09-{start_day + i:02d}", **kwargs) for i in range(count)]


def _rules(findings) -> list[str]:
    return [f.rule for f in findings]


# --- what it refuses to say -------------------------------------------------

def test_a_store_younger_than_a_week_gets_no_advice():
    """Six days is a first week, not a trend."""
    days = _series(engine.MIN_OBSERVED_DAYS - 1, revenue=50.0, orders=1)
    assert engine.analyze(days) == []


def test_a_window_without_revenue_produces_nothing():
    """Nothing was sold, so there is nothing to say about how it sold."""
    assert engine.analyze(_series(30)) == []


def test_missing_costs_reports_an_unknown_rather_than_a_zero():
    """Zero would read as "nothing at stake". The truth is that nobody knows."""
    days = _series(10, revenue=40.0, orders=1)
    finding = next(f for f in engine.analyze(days) if f.rule == "COSTS_MISSING")
    assert finding.money_at_stake is None


# --- costs ------------------------------------------------------------------

def test_costs_missing_fires_when_no_day_is_complete():
    days = _series(10, revenue=40.0, orders=1, costs_complete=False)
    assert "COSTS_MISSING" in _rules(engine.analyze(days))


def test_costs_missing_is_silent_once_every_day_is_complete():
    days = _series(10, revenue=40.0, orders=1, costs_complete=True)
    assert "COSTS_MISSING" not in _rules(engine.analyze(days))


def test_one_incomplete_day_is_enough_to_withhold_the_period():
    """A period's profit is only as sound as its worst day."""
    days = _series(9, revenue=40.0, orders=1, costs_complete=True)
    days.append(_day("2026-09-10", revenue=40.0, orders=1, costs_complete=False))
    assert "COSTS_MISSING" in _rules(engine.analyze(days))


# --- refunds ----------------------------------------------------------------

def test_refunds_name_the_money_that_was_actually_given_back():
    """The one figure this engine states, and it is measured, not forecast.

    Ten days at 30.00 is 300.00 of revenue; 45.00 refunded is 15%, over the
    threshold. The finding must carry 45.00 exactly — not a projection of what
    fixing the cause might recover.
    """
    days = _series(10, revenue=30.0, refunds=4.5, orders=2)
    finding = next(f for f in engine.analyze(days) if f.rule == "REFUNDS_HIGH")
    assert finding.money_at_stake == 45.0
    assert finding.detail["revenue"] == 300.0
    assert finding.detail["share"] == 0.15


def test_a_high_refund_ratio_on_pocket_change_is_ignored():
    """One refunded order out of three is not a refund problem."""
    days = _series(10, revenue=4.0, refunds=2.0, orders=1)   # 40.00 revenue, 50%
    assert "REFUNDS_HIGH" not in _rules(engine.analyze(days))


def test_refunds_below_the_share_threshold_stay_quiet():
    days = _series(10, revenue=100.0, refunds=5.0, orders=3)  # 5%, under 10%
    assert "REFUNDS_HIGH" not in _rules(engine.analyze(days))


def test_refunds_exactly_at_the_threshold_are_reported():
    """The boundary belongs to the seller, not to the silence."""
    days = _series(10, revenue=100.0, refunds=10.0, orders=3)  # exactly 10%
    assert "REFUNDS_HIGH" in _rules(engine.analyze(days))


# --- stalls -----------------------------------------------------------------

def test_a_run_of_silent_days_is_reported():
    selling = [_day("2026-09-01", revenue=200.0, orders=4)]
    quiet = [_day(f"2026-09-{2 + i:02d}") for i in range(engine.STALL_DAYS)]
    finding = next(f for f in engine.analyze(selling + quiet) if f.rule == "SALES_STALLED")
    assert finding.detail["days_without_orders"] == engine.STALL_DAYS
    assert finding.money_at_stake is None, "what the silence costs is not knowable here"


def test_a_store_that_sold_today_has_not_stalled():
    selling = [_day("2026-09-01", revenue=200.0, orders=4)]
    quiet = [_day(f"2026-09-{2 + i:02d}") for i in range(engine.STALL_DAYS)]
    today = [_day("2026-09-20", revenue=10.0, orders=1)]
    assert "SALES_STALLED" not in _rules(engine.analyze(selling + quiet + today))


def test_a_shorter_silence_is_not_called_a_stall():
    selling = [_day("2026-09-01", revenue=200.0, orders=4)]
    quiet = [_day(f"2026-09-{2 + i:02d}") for i in range(engine.STALL_DAYS - 1)]
    assert "SALES_STALLED" not in _rules(engine.analyze(selling + quiet))


# --- arithmetic and order ---------------------------------------------------

def test_the_window_totals_match_the_days_given():
    days = [
        _day("2026-09-01", revenue=10.0, refunds=1.0, orders=1, units=2, spend=3.0),
        _day("2026-09-02", revenue=20.5, refunds=0.5, orders=2, units=3, spend=1.5),
    ]
    assert engine.summarise(days) == {
        "days": 2, "revenue": 30.5, "refunds": 1.5, "spend": 4.5,
        "orders": 3, "units": 5, "costs_complete": False,
    }


def test_measured_money_is_listed_before_the_unknowns():
    """Ten minutes of attention belong where money is known to be going."""
    days = _series(10, revenue=30.0, refunds=4.5, orders=2)
    findings = engine.analyze(days)
    assert findings[0].rule == "REFUNDS_HIGH"
    assert findings[0].money_at_stake == 45.0
    assert all(f.money_at_stake is None for f in findings[1:])


def test_the_same_days_in_any_order_give_the_same_findings():
    """Deterministic: the engine sorts its own input, so the caller's ordering
    cannot change what a seller is told."""
    days = _series(10, revenue=30.0, refunds=4.5, orders=2)
    forwards = [f.to_dict() for f in engine.analyze(days)]
    backwards = [f.to_dict() for f in engine.analyze(list(reversed(days)))]
    assert forwards == backwards


def test_the_baseline_is_the_measured_window_a_result_is_compared_against():
    days = _series(10, revenue=30.0, orders=2, spend=2.0)
    finding = engine.analyze(days)[0]
    assert finding.baseline == {"days": 10, "revenue": 300.0, "spend": 20.0}


# --- stockout risk ----------------------------------------------------------

def _product(**kwargs) -> dict:
    """An active, physical, stock-counted product — the only kind restockable."""
    return {"product_id": "gid://1", "title": "Snowboard", "on_hand": 4,
            "units_sold": 20, "revenue": 400.0, "status": "ACTIVE",
            "is_gift_card": False, "requires_shipping": True, **kwargs}


# --- what may be restocked at all -------------------------------------------

def test_a_gift_card_is_never_proposed_for_restocking():
    """The live store's own catalogue: tracked, bought twice, minus two in
    stock. Every sum said restock it, and nobody restocks a gift card."""
    card = _product(title="Gift Card", is_gift_card=True, requires_shipping=False,
                    on_hand=-2, units_sold=2)
    assert engine.analyze_products([card], window_days=13) == []
    assert "gift card" in engine.restock_ineligibility(card)


def test_a_digital_product_has_no_shelf():
    download = _product(requires_shipping=False)
    assert engine.analyze_products([download], window_days=10) == []
    assert "digital" in engine.restock_ineligibility(download)


def test_a_product_that_does_not_say_whether_it_ships_is_refused():
    """Unknown is not a yes. Guessing here is how the gift card got through."""
    unknown = _product(requires_shipping=None)
    assert engine.analyze_products([unknown], window_days=10) == []
    assert "unknown is not a yes" in engine.restock_ineligibility(unknown)


def test_an_archived_product_is_not_worth_restocking():
    archived = _product(status="ARCHIVED")
    assert engine.analyze_products([archived], window_days=10) == []
    assert "archived" in engine.restock_ineligibility(archived)


def test_a_draft_product_is_not_worth_restocking():
    assert engine.analyze_products([_product(status="DRAFT")], window_days=10) == []


def test_an_eligible_product_has_nothing_held_against_it():
    assert engine.restock_ineligibility(_product()) is None


def test_a_product_running_out_soon_is_reported():
    """20 units over 10 days is 2 a day; 4 left is two days of stock."""
    finding = engine.analyze_products([_product()], window_days=10)[0]
    assert finding.rule == "STOCKOUT_RISK"
    assert finding.detail["days_left"] == 2.0
    assert finding.detail["daily_units"] == 2.0
    assert finding.severity == "critical"


def test_the_cost_of_running_out_is_not_claimed():
    """It depends on how long the shelf stays empty, which nobody knows yet."""
    finding = engine.analyze_products([_product()], window_days=10)[0]
    assert finding.money_at_stake is None


def test_oversold_stock_is_reported_as_gone_not_as_negative_days():
    """Shopify reports oversold stock as a negative number. Dividing by the
    sales rate would promise the shelf empties in minus thirteen days."""
    finding = engine.analyze_products([_product(on_hand=-2)], window_days=10)[0]
    assert finding.detail["days_left"] == 0.0
    assert "out of stock" in finding.title
    assert "-2" in finding.reason, "the real number still has to be visible"


def test_a_well_stocked_product_is_left_alone():
    """50 units at 2 a day is 25 days of cover — past the horizon."""
    assert engine.analyze_products([_product(on_hand=50)], window_days=10) == []


def test_a_product_nobody_bought_has_no_rate_to_project():
    """Dividing stock by an anecdote produces a confident number from nothing."""
    assert engine.analyze_products([_product(units_sold=1)], window_days=10) == []


def test_untracked_inventory_is_skipped_rather_than_assumed_empty():
    """Shopify returns nothing for a product it does not count. Treating that as
    zero would report every such product as about to run out."""
    assert engine.analyze_products([_product(on_hand=None)], window_days=10) == []


def test_a_window_shorter_than_a_week_produces_no_stockout_advice():
    assert engine.analyze_products([_product()], window_days=3) == []


def test_the_soonest_to_run_out_comes_first():
    soon = _product(product_id="gid://soon", title="Soon", on_hand=2)      # 1 day
    later = _product(product_id="gid://later", title="Later", on_hand=20)  # 10 days
    titles = [f.detail["title"] for f in engine.analyze_products([later, soon], window_days=10)]
    assert titles == ["Soon", "Later"]


def test_the_inventory_it_was_found_at_travels_in_the_baseline():
    """The read-back that proves a restock happened compares against this."""
    finding = engine.analyze_products([_product()], window_days=10)[0]
    assert finding.baseline["on_hand"] == 4
    assert finding.baseline == {"days": 10, "revenue": 400.0, "spend": 0.0,
                                "on_hand": 4, "product_id": "gid://1"}


# --- turning findings into actions ------------------------------------------

from app import actions_engine  # noqa: E402  (read after the engine it consumes)


def _proposals(days: list[dict]):
    findings = [f.to_dict() for f in engine.analyze(days)]
    return actions_engine.proposals_from_commerce(findings, target="shop.myshopify.com")


def test_an_unpriceable_finding_reaches_the_action_still_unpriced():
    """The silence has to survive the trip. 0.0 here would put "no money at
    stake" on the screen, which is a claim nobody checked."""
    proposal = next(p for p in _proposals(_series(10, revenue=40.0, orders=1))
                    if p.action_type == "ENTER_LANDED_COSTS")
    assert proposal.projected_impact is None
    assert proposal.module == "commerce"


def test_measured_refunds_become_the_projected_impact():
    proposal = next(p for p in _proposals(_series(10, revenue=30.0, refunds=4.5, orders=2))
                    if p.action_type == "REVIEW_REFUNDS")
    assert proposal.projected_impact == 45.0


def test_priced_actions_come_before_unpriced_ones():
    proposals = _proposals(_series(10, revenue=30.0, refunds=4.5, orders=2))
    assert proposals[0].action_type == "REVIEW_REFUNDS"
    assert all(p.projected_impact is None for p in proposals[1:])


def test_an_action_the_operator_cannot_undo_says_so():
    """Entering costs is something a person does; offering a revert path that
    does not exist would be a promise the Operator cannot keep."""
    proposal = _proposals(_series(10, revenue=40.0, orders=1))[0]
    assert proposal.revert_to == {"revertible": False}


def test_a_finding_with_no_known_action_is_skipped_not_invented():
    proposals = actions_engine.proposals_from_commerce(
        [{"rule": "SOMETHING_NEW", "money_at_stake": 10.0, "baseline": {}, "reason": ""}],
        target="shop.myshopify.com")
    assert proposals == []


def test_the_measured_window_travels_into_the_action():
    """Link 6 prices the result by comparing against exactly this."""
    proposal = _proposals(_series(10, revenue=30.0, orders=2, spend=2.0))[0]
    assert proposal.baseline == {"days": 10, "revenue": 300.0, "spend": 20.0}


def test_a_restock_names_the_product_not_the_shop():
    """Every restock titled with the shop domain would be indistinguishable in
    the queue."""
    findings = [f.to_dict() for f in engine.analyze_products([_product()], window_days=10)]
    proposal = actions_engine.proposals_from_commerce(
        findings, target="shop.myshopify.com")[0]
    assert proposal.action_type == "RESTOCK_PRODUCT"
    assert proposal.target == "Snowboard"
    assert proposal.projected_impact is None


def test_only_a_restock_may_ever_be_called_proven():
    """The others are things a person does out of our sight. However carefully
    their result is measured, nobody watched them happen."""
    assert actions_engine.VERIFIABLE_COMMERCE_ACTIONS == {"RESTOCK_PRODUCT"}
    for action_type in ("ENTER_LANDED_COSTS", "REVIEW_REFUNDS", "REVIEW_STALLED_SALES"):
        assert action_type not in actions_engine.VERIFIABLE_COMMERCE_ACTIONS
