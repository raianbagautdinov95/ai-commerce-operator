"""What a store's own sales say, and what they refuse to say.

The Operator has been able to import Shopify sales for a while and has never
been able to do anything with them: nothing read `commerce_daily_metrics`, so a
seller could connect a store, sync real orders, and watch the proposals screen
stay empty for ever. This is the missing half — the deterministic part that
turns measured days into findings a human can act on.

Three rules, and the discipline is in what they refuse to claim:

- **Costs missing.** Revenue is arriving and not one day carries complete costs,
  so profit cannot be computed at all. Every other money question waits behind
  this one. The money at stake is unknown, and is reported as unknown.
- **Refunds.** Money that has already left, measured, not predicted. This is the
  only rule that names a figure, and the figure is what was observed.
- **Sales stalled.** The store sold, and then stopped. What that costs depends
  on why it stopped, which these numbers do not know, so no figure is offered.

Two rules out of three decline to name a number. That is the point: the engine
is allowed to say "this is wrong" without also inventing what fixing it is
worth. A projection dressed as a finding is how a tool starts lying.

Nothing here touches the database, the network, or an LLM: same days in, same
findings out, in the same order. Whether any of it may be acted on unattended is
the guardrails' decision, not this module's.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

#: Below this many observed days a store has a first week, not a trend. Every
#: rule stays silent: advice from four days of data is noise wearing a suit.
MIN_OBSERVED_DAYS = 7

#: Refunds are only worth an action once there is enough revenue for the ratio
#: to mean anything. One refunded order out of three is not a refund problem.
MIN_REVENUE_FOR_REFUND_RULE = 100.0

#: Share of revenue given back before it is worth a human's attention.
REFUND_SHARE_THRESHOLD = 0.10

#: Consecutive most-recent days without a single order that count as a stall.
STALL_DAYS = 7


@dataclass
class Finding:
    """One thing the numbers support saying, and nothing beyond it."""
    rule: str
    severity: str                    # info | warning | critical
    title: str
    reason: str
    #: Money already lost that this finding is about, when it was measured.
    #: None means the cost is genuinely unknown — never zero, which would read
    #: as "nothing at stake".
    money_at_stake: float | None
    #: The measured window this was found in, in the shape `actions_engine`
    #: compares against later: {days, revenue, spend}.
    baseline: dict[str, Any] = field(default_factory=dict)
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _number(row: dict, key: str) -> float:
    try:
        return float(row.get(key) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _count(row: dict, key: str) -> int:
    try:
        return int(row.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def summarise(days: list[dict]) -> dict[str, Any]:
    """Total one store's window. Pure arithmetic over what was measured."""
    revenue = round(sum(_number(day, "revenue") for day in days), 2)
    refunds = round(sum(_number(day, "refunds") for day in days), 2)
    spend = round(sum(_number(day, "advertising_spend") for day in days), 2)
    return {
        "days": len(days),
        "revenue": revenue,
        "refunds": refunds,
        "spend": spend,
        "orders": sum(_count(day, "orders") for day in days),
        "units": sum(_count(day, "units") for day in days),
        # Judged on the days that actually sold something. A quiet day has no
        # cost to report, and counting it as incomplete would accuse a seller
        # who had filled in everything there was to fill in.
        "costs_complete": _costs_complete(days),
    }


def _sold_something(day: dict) -> bool:
    return _number(day, "revenue") > 0 or _count(day, "orders") > 0


def _costs_complete(days: list[dict]) -> bool:
    """True when every day that sold something carries its costs.

    One incomplete selling day is enough to make the period's profit a guess,
    so this is `all`, not a majority.
    """
    selling = [day for day in days if _sold_something(day)]
    return bool(selling) and all(bool(day.get("costs_complete")) for day in selling)


def daily_series(rows: list[dict], *, today) -> list[dict]:
    """One entry per calendar day, oldest first, with the quiet days filled in.

    Days that sold nothing are simply absent from storage, and their absence
    misleads twice over: a chart drawn from the raw rows runs revenue straight
    across a fortnight nobody bought anything in, and a stall becomes invisible
    because there are no silent days left to count.

    Filling starts at the first day observed and never earlier. A quiet day
    inside the window is a real zero; a day before anything was ever recorded is
    unknown, and calling it zero would invent an observation nobody made.

    Several channels selling on one date fold into that date once.
    """
    import datetime as _dt

    merged: dict[Any, dict] = {}
    for row in rows:
        date = row.get("date")
        if date is None:
            continue
        day = merged.setdefault(date, {
            "date": date, "revenue": 0.0, "refunds": 0.0, "advertising_spend": 0.0,
            "orders": 0, "units": 0, "costs_complete": True,
        })
        day["revenue"] += _number(row, "revenue")
        day["refunds"] += _number(row, "refunds")
        day["advertising_spend"] += _number(row, "advertising_spend")
        day["orders"] += _count(row, "orders")
        day["units"] += _count(row, "units")
        # One channel missing its costs leaves the whole day incomplete.
        day["costs_complete"] = day["costs_complete"] and bool(row.get("costs_complete"))

    if not merged:
        return []

    date = min(merged)
    while date <= today:
        merged.setdefault(date, {
            "date": date, "revenue": 0.0, "refunds": 0.0, "advertising_spend": 0.0,
            # A day with nothing sold has no costs outstanding. Marking it
            # incomplete would report a gap that does not exist.
            "orders": 0, "units": 0, "costs_complete": True,
        })
        date += _dt.timedelta(days=1)

    series = []
    for date in sorted(merged):
        day = merged[date]
        series.append({**day, "revenue": round(day["revenue"], 2),
                       "refunds": round(day["refunds"], 2),
                       "advertising_spend": round(day["advertising_spend"], 2)})
    return series


def _baseline(summary: dict[str, Any]) -> dict[str, Any]:
    """The measured window, in the shape a later measurement compares against."""
    return {"days": summary["days"], "revenue": summary["revenue"],
            "spend": summary["spend"]}


def analyze(days: list[dict]) -> list[Finding]:
    """Findings for one store's window, worst first.

    `days` is one entry per calendar day, oldest first, as the commerce dashboard
    builds it — quiet days included. Their absence would make a stall invisible.
    """
    ordered = sorted(days, key=lambda day: str(day.get("date", "")))
    summary = summarise(ordered)
    if summary["days"] < MIN_OBSERVED_DAYS or summary["revenue"] <= 0:
        return []

    findings: list[Finding] = []

    if not summary["costs_complete"]:
        findings.append(Finding(
            rule="COSTS_MISSING",
            severity="critical",
            title="Shop profit cannot be computed: daily costs are missing",
            reason=(
                f"{summary['orders']} orders brought {summary['revenue']:.2f} over "
                f"{summary['days']} days, and not one day carries complete costs. "
                "Until they do, every profit figure is withheld rather than guessed, "
                "and no advice about margin can be trusted. This is the shop's day "
                "— fees, advertising, refunds and cost of goods together — and it "
                "is a different question from what one unit of one product cost to "
                "buy, which is entered per product and is what a restock's result "
                "is measured with. Neither stands in for the other."
            ),
            # What entering costs is worth cannot be known before they are
            # entered. Naming a figure here would be the invention this engine
            # exists to avoid.
            money_at_stake=None,
            baseline=_baseline(summary),
            detail={"orders": summary["orders"], "revenue": summary["revenue"],
                    "days_observed": summary["days"]},
        ))

    if (summary["revenue"] >= MIN_REVENUE_FOR_REFUND_RULE
            and summary["refunds"] > 0
            and summary["refunds"] / summary["revenue"] >= REFUND_SHARE_THRESHOLD):
        share = summary["refunds"] / summary["revenue"]
        findings.append(Finding(
            rule="REFUNDS_HIGH",
            severity="warning",
            title="Refunds are taking back a large share of revenue",
            reason=(
                f"{summary['refunds']:.2f} of {summary['revenue']:.2f} was refunded "
                f"over {summary['days']} days — {share * 100:.1f}%. That money has "
                "already left; this is what was measured, not a forecast of what "
                "fixing the cause would return."
            ),
            money_at_stake=summary["refunds"],
            baseline=_baseline(summary),
            detail={"refunds": summary["refunds"], "revenue": summary["revenue"],
                    "share": round(share, 4)},
        ))

    stalled = _trailing_days_without_orders(ordered)
    if stalled >= STALL_DAYS and summary["orders"] > 0:
        findings.append(Finding(
            rule="SALES_STALLED",
            severity="warning",
            title=f"No orders for {stalled} days",
            reason=(
                f"The store sold earlier in this window and has had no orders for "
                f"{stalled} days. What the silence costs depends on why it happened, "
                "which these numbers do not say, so no figure is claimed."
            ),
            money_at_stake=None,
            baseline=_baseline(summary),
            detail={"days_without_orders": stalled, "days_observed": summary["days"]},
        ))

    # Findings that name measured money come first: a human with ten minutes
    # should spend them where money is known to be going.
    findings.sort(key=lambda f: (f.money_at_stake is None, -(f.money_at_stake or 0.0)))
    return findings


#: A product is at risk when what is left on the shelf would not outlast this
#: many days at the rate it has actually been selling.
STOCKOUT_HORIZON_DAYS = 14

#: Fewer units than this in the window is not a sales rate, it is an anecdote.
#: Dividing stock by it would produce a confident number from nothing.
MIN_UNITS_FOR_STOCKOUT_RULE = 2


def restock_ineligibility(product: dict) -> str | None:
    """Why this product must never be proposed for restocking, or None.

    The first live store sold a gift card. It tracked inventory, it had been
    bought twice, and Shopify reported minus two in stock, so every arithmetic
    test said "running out, restock it". The sums were right and the advice was
    absurd: nobody restocks a gift card, and a seller shown that learns the
    Operator does not understand their shop.

    Each answer is a sentence rather than a boolean because a proposal withheld
    without a reason looks like a proposal that was never found.
    """
    if product.get("is_gift_card"):
        return "a gift card is issued, not restocked"
    requires_shipping = product.get("requires_shipping")
    if requires_shipping is None:
        return "nothing says whether this ships, and unknown is not a yes"
    if not requires_shipping:
        return "a digital product has no shelf to refill"
    status = str(product.get("status") or "").upper()
    if status and status != "ACTIVE":
        return f"the product is {status.lower()}, so nobody can buy it anyway"
    if product.get("on_hand") is None:
        return "this channel does not count its stock"
    return None


def analyze_products(products: list[dict], *, window_days: int) -> list[Finding]:
    """Which products run out soon at the rate they have actually been selling.

    Deliberately narrower than `inventory_engine`, which answers the fuller
    question — when to reorder, how much, what the capital is worth — and needs
    a supplier lead time to do it. Shopify does not know that lead time and
    neither do we, and every verdict in that engine turns on it. Rather than
    invent one and inherit a confident answer built on a guess, this rule uses
    only what was measured: units sold, and units left.

    `products` carries one entry per product for the window:
    {product_id, title, on_hand, units_sold, revenue}.
    """
    if window_days < MIN_OBSERVED_DAYS:
        return []

    findings: list[Finding] = []
    for product in products:
        units = _count(product, "units_sold")
        on_hand = _count(product, "on_hand")
        if units < MIN_UNITS_FOR_STOCKOUT_RULE:
            continue
        # A restock has to be a thing the seller could actually do. Untracked
        # stock, archived products and anything digital are refused here rather
        # than talked out of it later.
        if restock_ineligibility(product) is not None:
            continue

        daily = units / window_days
        # Shopify reports oversold stock as a negative number. Dividing by the
        # rate would give a negative runway and a sentence about running out in
        # minus thirteen days; there is no runway left to describe.
        days_left = max(on_hand, 0) / daily
        if days_left > STOCKOUT_HORIZON_DAYS:
            continue
        already_out = on_hand <= 0

        revenue = _number(product, "revenue")
        title = str(product.get("title") or product.get("product_id") or "")
        findings.append(Finding(
            rule="STOCKOUT_RISK",
            severity="critical" if days_left <= STOCKOUT_HORIZON_DAYS / 2 else "warning",
            title=(f"{title} is out of stock" if already_out
                   else f"{title} runs out in about {days_left:.0f} days"),
            reason=(
                f"{units} sold over {window_days} days is {daily:.2f} a day, and "
                + (f"Shopify reports {on_hand} left — it is already unavailable"
                   if already_out
                   else f"{on_hand} are left, about {days_left:.0f} days of stock")
                + f". It earned {revenue:.2f} in that window, which is what stops "
                "while the shelf is empty. How much that costs depends on how long "
                "it stays empty, so no figure is claimed here."
            ),
            # The loss depends on how long the stockout lasts and how fast it is
            # fixed. Neither is knowable now, and the measured result at the end
            # is the only money this action may ever claim.
            money_at_stake=None,
            # `on_hand` rides along in the baseline because it is part of the
            # "before" picture too: the read-back that proves the restock
            # happened compares against exactly this number. MetricWindow reads
            # days/revenue/spend and ignores the rest, so nothing else changes.
            baseline={"days": window_days, "revenue": round(revenue, 2), "spend": 0.0,
                      "on_hand": on_hand,
                      "product_id": str(product.get("product_id") or "")},
            detail={"product_id": str(product.get("product_id") or ""), "title": title,
                    "on_hand": on_hand, "units_sold": units,
                    "daily_units": round(daily, 3), "days_left": round(days_left, 1)},
        ))

    # Soonest to run out first.
    findings.sort(key=lambda f: f.detail["days_left"])
    return findings


def _trailing_days_without_orders(ordered: list[dict]) -> int:
    """How many of the most recent days had no order at all."""
    stalled = 0
    for day in reversed(ordered):
        if _count(day, "orders") > 0:
            break
        stalled += 1
    return stalled
