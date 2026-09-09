"""
Operator payroll — the deterministic proof that the Operator earns its keep.

Every other engine PROPOSES; this one measures what actually happened after a
proposal was applied, in money. It is the answer to the only question the owner
of a store really asks: *did this thing pay for itself this month?*

As everywhere else in the codebase, the arithmetic here is plain and auditable
and the LLM never touches it — it may narrate a result, never produce one.

How impact is computed
----------------------
An action carries two metric snapshots taken over explicit windows:

    baseline = {"days": 14, "spend": 120.50, "revenue": 12.00}   # before applying
    outcome  = {"days": 14, "spend":   8.00, "revenue":  6.00}   # after applying

Both are converted to DAILY RATES before comparison, so windows of different
lengths stay comparable, and the impact is the net of two effects:

    cost avoided  = (baseline spend/day - outcome spend/day) * outcome days
    revenue gained= (outcome revenue/day - baseline revenue/day) * outcome days
    impact        = cost avoided + revenue gained

Netting matters. Cutting 112 EUR of ad spend while losing 6 EUR of sales is worth
106 EUR, not 112 — an engine that reported the gross number would flatter itself.
A negative impact is reported as readily as a positive one; an Operator that
cannot show its losses cannot be trusted with its wins.

Results are PROVISIONAL until the outcome window is at least as long as the
window the action asked for. Provisional impact is computed but excluded from
the headline total, so the number the owner sees is only settled evidence.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from decimal import ROUND_DOWN, Decimal
from enum import Enum
from typing import Any

# An outcome window shorter than this proves nothing, whatever it shows.
MIN_MEASURABLE_DAYS = 3


class ActionStatus(str, Enum):
    PROPOSED = "proposed"      # the engine suggested it; nobody has acted
    APPLIED = "applied"        # it is in effect; baseline captured, awaiting outcome
    MEASURED = "measured"      # outcome captured and priced
    DISMISSED = "dismissed"    # a human declined it
    REVERTED = "reverted"      # it was applied and then undone


@dataclass
class MetricWindow:
    """Money observed over a stretch of days. Days must be positive to have a rate."""
    days: int
    spend: float = 0.0
    revenue: float = 0.0

    @classmethod
    def from_dict(cls, raw: dict | None) -> "MetricWindow | None":
        if not raw:
            return None
        try:
            days = int(raw.get("days", 0))
        except (TypeError, ValueError):
            return None
        if days <= 0:
            return None
        return cls(days=days, spend=float(raw.get("spend", 0.0) or 0.0),
                   revenue=float(raw.get("revenue", 0.0) or 0.0))

    @property
    def spend_per_day(self) -> float:
        return self.spend / self.days

    @property
    def revenue_per_day(self) -> float:
        return self.revenue / self.days


@dataclass
class Impact:
    cost_avoided: float
    revenue_gained: float
    net: float
    provisional: bool
    basis: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def measure(baseline: dict | None, outcome: dict | None, *,
            requested_days: int = 14) -> Impact | None:
    """Price one applied action. Returns None when there is nothing to compare."""
    before = MetricWindow.from_dict(baseline)
    after = MetricWindow.from_dict(outcome)
    if before is None or after is None:
        return None
    if after.days < MIN_MEASURABLE_DAYS:
        return None

    cost_avoided = (before.spend_per_day - after.spend_per_day) * after.days
    revenue_gained = (after.revenue_per_day - before.revenue_per_day) * after.days
    provisional = after.days < requested_days
    basis = (f"{after.days}d observed vs {before.days}d baseline"
             + (f"; window not yet complete ({requested_days}d requested)" if provisional else ""))
    return Impact(
        cost_avoided=round(cost_avoided, 2),
        revenue_gained=round(revenue_gained, 2),
        net=round(cost_avoided + revenue_gained, 2),
        provisional=provisional,
        basis=basis,
    )


#: A restock that sells nothing beyond the stock it replaced has proved nothing,
#: however long the window ran. Kept separate from MIN_MEASURABLE_DAYS: one is
#: about time, this is about evidence.
MIN_ATTRIBUTABLE_UNITS = 1

#: Why a restock could be observed and still not priced. Each one is a real
#: situation with a real answer, and none of them is zero.
COST_UNKNOWN = "cost_unknown"
CURRENCY_MISMATCH = "currency_mismatch"
WINDOW_TOO_SHORT = "window_too_short"
NOTHING_TO_COMPARE = "nothing_to_compare"

#: Money is rounded once, at the end, downwards. Never up: an eighth of a penny
#: in our favour on every claim is still a claim nobody measured.
_MONEY = Decimal("0.01")


@dataclass(frozen=True)
class SoldUnits:
    """One variant's sales inside the window, already netted.

    `revenue` is what the shop kept for these units: after discounts, after tax
    where the shop's prices include it, and after refunds. `units` excludes what
    came back. `unit_cost` is None when nobody has said what a unit cost — which
    is not zero, and is the difference between a proven margin and the whole
    sale price called profit.
    """
    variant_id: str
    units: int
    revenue: Decimal
    unit_cost: Decimal | None
    #: The currency the sale was in.
    currency: str
    variant_title: str | None = None
    #: The currency the cost was recorded in. Kept apart from the sale's on
    #: purpose: a cost in one currency and a sale in another cannot be
    #: subtracted, and quietly treating them as equal is how a margin comes out
    #: of thin air.
    cost_currency: str | None = None

    @property
    def unit_revenue(self) -> Decimal:
        if self.units <= 0:
            return Decimal("0")
        return self.revenue / Decimal(self.units)

    @property
    def unit_margin(self) -> Decimal:
        return self.unit_revenue - (self.unit_cost or Decimal("0"))


@dataclass
class RestockImpact:
    """What a witnessed restock can be said to have caused, and what it cannot."""
    attributable_units: int
    attributable_margin: Decimal
    attributable_revenue: Decimal
    attributable_cost: Decimal
    observed_units: int
    observed_revenue: Decimal
    stock_before: int
    days: int
    currency: str
    provisional: bool
    basis: str

    def to_dict(self) -> dict[str, Any]:
        # Floats at the boundary only: this lands in a JSON column and is read
        # back by `build_payroll`. Every sum above was Decimal.
        return {
            "attributable_units": self.attributable_units,
            "attributable_margin": float(self.attributable_margin),
            "attributable_revenue": float(self.attributable_revenue),
            "attributable_cost": float(self.attributable_cost),
            "observed_units": self.observed_units,
            "observed_revenue": float(self.observed_revenue),
            "stock_before": self.stock_before,
            "days": self.days,
            "currency": self.currency,
            "provisional": self.provisional,
            # What `build_payroll` adds up. The same number as the margin, named
            # the way the payroll expects; nothing else in this dict is a total.
            "net": float(self.attributable_margin),
            "basis": self.basis,
        }


@dataclass
class RestockMeasurement:
    """Either a priced result, or the exact reason there is not one."""
    impact: RestockImpact | None
    blocked: str | None
    reason: str


def _money(value: Decimal) -> Decimal:
    return value.quantize(_MONEY, rounding=ROUND_DOWN)


def measure_restock(baseline: dict | None, sales: list[SoldUnits], *,
                    days: int, requested_days: int = 14) -> RestockMeasurement:
    """Price a restock by the margin on units that could not otherwise have sold.

    The counterfactual is the whole argument, and it needs no control group: a
    shop holding one unit could not have sold thirty. Units sold in the window
    beyond the stock that already existed are units the restock made possible,
    and they are the only ones this claims.

    What is deliberately refused:

    * The before-and-after revenue rate. Sales did rise after the shelf was
      refilled; they also rose after that Tuesday. Attributing the difference to
      the restock is the post-hoc reasoning this product exists to avoid.
    * Any unit whose cost nobody has supplied. One unpriceable variant blocks
      the whole result rather than being quietly left out, because leaving it
      out would raise the average margin of what remains.
    * Two currencies in one sum.

    Which units count as the ones the old shelf could have covered is a choice,
    and it is made against ourselves: the highest-margin units are assumed to
    have sold anyway, so what is claimed is the cheapest explanation of the
    same sales.
    """
    if not baseline:
        return RestockMeasurement(None, NOTHING_TO_COMPARE,
                                  "There is nothing to compare against.")
    try:
        stock_before = int(baseline.get("on_hand"))
    except (TypeError, ValueError):
        return RestockMeasurement(None, NOTHING_TO_COMPARE,
                                  "No stock level was recorded before the change.")
    if days < MIN_MEASURABLE_DAYS:
        return RestockMeasurement(
            None, WINDOW_TOO_SHORT,
            f"{max(days, 0)} full day(s) is below the {MIN_MEASURABLE_DAYS} that "
            f"prove anything.")

    selling = [row for row in sales if row.units > 0]
    observed_units = sum(row.units for row in selling)
    observed_revenue = sum((row.revenue for row in selling), Decimal("0"))

    currencies = {row.currency.upper() for row in selling if row.currency}
    if len(currencies) > 1:
        return RestockMeasurement(
            None, CURRENCY_MISMATCH,
            "These sales are in more than one currency "
            f"({', '.join(sorted(currencies))}), and adding them up would invent "
            "an exchange rate nobody supplied.")
    currency = next(iter(currencies), (baseline.get("currency") or "USD").upper())

    # Shopify reports oversold stock as a negative number. Nothing was on the
    # shelf, so nothing could have been sold from it.
    available_before = max(stock_before, 0)
    incremental_total = max(0, observed_units - available_before)

    if incremental_total == 0:
        # A finished answer, and the answer is nothing. Not a failure and not a
        # reason to keep waiting: the fortnight ran and the restock has not yet
        # been the reason for a single sale.
        return RestockMeasurement(
            RestockImpact(
                attributable_units=0, attributable_margin=Decimal("0"),
                attributable_revenue=Decimal("0"), attributable_cost=Decimal("0"),
                observed_units=observed_units, observed_revenue=_money(observed_revenue),
                stock_before=stock_before, days=days, currency=currency,
                provisional=days < requested_days,
                basis=(f"{observed_units} sold in {days} day(s), which the "
                       f"{available_before} already on the shelf could have "
                       f"covered. Nothing here needed the restock to happen."),
            ), None, "")

    missing = [row for row in selling if row.unit_cost is None]
    if missing:
        names = ", ".join(sorted(
            (row.variant_title or row.variant_id) for row in missing)[:3])
        return RestockMeasurement(
            None, COST_UNKNOWN,
            f"{len(missing)} variant(s) sold in this window with no unit cost on "
            f"record ({names}). Leaving them out would raise the average margin "
            f"of the ones that remain, so nothing is claimed until they have one.")

    mixed = [row for row in selling
             if row.unit_cost is not None and row.cost_currency
             and row.cost_currency.upper() != currency]
    if mixed:
        others = ", ".join(sorted({str(row.cost_currency).upper() for row in mixed}))
        return RestockMeasurement(
            None, CURRENCY_MISMATCH,
            f"The sales are in {currency} and a cost is recorded in {others}. "
            f"Subtracting one from the other would invent an exchange rate "
            f"nobody supplied.")

    # The cheapest explanation of the same sales: assume the units the old shelf
    # could have covered were the most profitable ones.
    ranked = sorted(selling, key=lambda row: row.unit_margin, reverse=True)
    covered = available_before
    attributable_units = 0
    attributable_revenue = Decimal("0")
    attributable_cost = Decimal("0")
    for row in ranked:
        take = row.units
        if covered:
            skipped = min(covered, take)
            covered -= skipped
            take -= skipped
        if take <= 0:
            continue
        attributable_units += take
        attributable_revenue += row.unit_revenue * Decimal(take)
        attributable_cost += (row.unit_cost or Decimal("0")) * Decimal(take)

    margin = _money(attributable_revenue - attributable_cost)
    provisional = days < requested_days
    basis = (f"{observed_units} sold in {days} day(s) against {available_before} "
             f"in stock beforehand, so {attributable_units} could not have been "
             f"sold without the restock. Margin on those units only, after "
             f"discounts, tax and refunds; before shipping, payment fees and "
             f"anything else the shop pays per order.")
    if provisional:
        basis += f" Window not yet complete ({requested_days}d requested)."

    return RestockMeasurement(
        RestockImpact(
            attributable_units=attributable_units,
            attributable_margin=margin,
            attributable_revenue=_money(attributable_revenue),
            attributable_cost=_money(attributable_cost),
            observed_units=observed_units,
            observed_revenue=_money(observed_revenue),
            stock_before=stock_before, days=days, currency=currency,
            provisional=provisional, basis=basis,
        ), None, "")


@dataclass
class Payroll:
    """The Operator's own P&L for a period."""
    settled_impact: float          # measured, complete windows — the defensible number
    provisional_impact: float      # measured but still inside its window
    operator_cost: float           # what the Operator charged over the same period
    net: float                     # settled_impact - operator_cost
    paid_for_itself: bool
    counts: dict[str, int] = field(default_factory=dict)
    awaiting_measurement: int = 0
    currency: str = "USD"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_payroll(actions: list[dict], *, operator_cost: float,
                  currency: str = "USD") -> Payroll:
    """Aggregate priced actions into the headline 'did it pay for itself' answer.

    Each action dict carries its own status and, when measured, its impact:
        {"status": "measured", "impact": {"net": 106.0, "provisional": False}}
    Only settled impact reaches the headline; provisional sits beside it, visible
    but not counted.
    """
    settled = provisional = 0.0
    counts: dict[str, int] = {}
    awaiting = 0
    for action in actions:
        status = str(action.get("status", ""))
        counts[status] = counts.get(status, 0) + 1
        if status == ActionStatus.APPLIED.value:
            awaiting += 1
        impact = action.get("impact") or {}
        net = impact.get("net")
        if status != ActionStatus.MEASURED.value or net is None:
            continue
        if impact.get("provisional"):
            provisional += float(net)
        else:
            settled += float(net)

    settled = round(settled, 2)
    operator_cost = round(float(operator_cost), 2)
    return Payroll(
        settled_impact=settled,
        provisional_impact=round(provisional, 2),
        operator_cost=operator_cost,
        net=round(settled - operator_cost, 2),
        paid_for_itself=settled > operator_cost,
        counts=counts,
        awaiting_measurement=awaiting,
        currency=currency,
    )


# --- Turning PPC findings into proposals -------------------------------------

@dataclass
class Proposal:
    """One action the Operator wants to open, derived from a finding.

    Deterministic: the same report and the same engine verdict always produce the
    same proposals, in the same order. Nothing here decides whether it may be
    applied — that is the guardrails' job.
    """
    module: str
    action_type: str
    target: str
    #: None where the money cannot honestly be predicted. Zero would read as
    #: "nothing at stake", which is a different and usually false claim.
    projected_impact: float | None
    baseline: dict
    revert_to: dict
    note: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# A search term with no orders is wasted spend; one that merely converts badly is
# a bid problem, and bids are not what the Operator is trusted with yet.
NEGATABLE_ACTIONS = {"NEGATE"}


def proposals_from_ppc(findings: list[dict], rows: list[dict], *, window_days: int,
                       min_spend: float = 1.0) -> list[Proposal]:
    """Pair engine verdicts with the report rows that carry the ids to act on.

    A finding without a matching row cannot be acted on — there is no campaign or
    ad group to attach a negative keyword to — so it is skipped rather than
    guessed at. Terms below `min_spend` are skipped too: the paperwork of an
    action costs more attention than they are worth.
    """
    by_term = {str(row.get("search_term", "")).strip().lower(): row for row in rows}
    proposals: list[Proposal] = []
    for finding in findings:
        if str(finding.get("action", "")) not in NEGATABLE_ACTIONS:
            continue
        term = str(finding.get("keyword", "")).strip()
        row = by_term.get(term.lower())
        if row is None:
            continue
        campaign_id = str(row.get("campaign_id") or "")
        ad_group_id = str(row.get("ad_group_id") or "")
        if not campaign_id or not ad_group_id:
            continue
        spend = float(finding.get("spend", 0.0) or 0.0)
        if spend < min_spend:
            continue
        proposals.append(Proposal(
            module="ppc",
            action_type="NEGATE_KEYWORD",
            target=term,
            # Wasted spend over the window is what negating it stops.
            projected_impact=round(float(finding.get("savings", spend) or spend), 2),
            baseline={"days": window_days, "spend": round(spend, 2),
                      "revenue": round(float(finding.get("sales", 0.0) or 0.0), 2)},
            # Undoing a negative keyword means removing it again.
            revert_to={"remove_negative_keyword": term, "campaign_id": campaign_id,
                       "ad_group_id": ad_group_id},
            note=str(finding.get("reason", ""))[:500],
        ))
    # Biggest waste first: if a daily limit bites, it should bite the small ones.
    proposals.sort(key=lambda p: -(p.projected_impact or 0.0))
    return proposals


#: What each commerce finding asks a human to do. A rule with no entry here is a
#: finding the Operator can describe but not yet turn into an action, and it is
#: skipped rather than given an invented one.
COMMERCE_ACTIONS = {
    "COSTS_MISSING": "ENTER_LANDED_COSTS",
    "REFUNDS_HIGH": "REVIEW_REFUNDS",
    "SALES_STALLED": "REVIEW_STALLED_SALES",
    "STOCKOUT_RISK": "RESTOCK_PRODUCT",
}

#: Actions whose completion can be witnessed by reading the store back through
#: its own API, rather than taken on the seller's word. Only these may ever
#: reach `evidence_mode="real"`; everything else stays unverified however
#: carefully its result is measured, and the payroll keeps excluding it.
VERIFIABLE_COMMERCE_ACTIONS = {"RESTOCK_PRODUCT"}


def proposals_from_commerce(findings: list[dict], *, target: str) -> list[Proposal]:
    """Turn commerce findings into actions, carrying their silences with them.

    Two of the three commerce rules decline to price themselves, and that has to
    survive the trip into an action: `projected_impact` stays None rather than
    becoming 0.0, so the screen can say the money is unknown instead of implying
    there is none at stake.

    None of these can be undone by the Operator — entering costs or looking into
    refunds are things a person does — so each says so rather than offering a
    revert path that does not exist.
    """
    proposals: list[Proposal] = []
    for finding in findings:
        action_type = COMMERCE_ACTIONS.get(str(finding.get("rule", "")))
        if action_type is None:
            continue
        stake = finding.get("money_at_stake")
        detail = finding.get("detail") or {}
        # A product-level finding acts on that product, not on the shop. Naming
        # the shop would put every restock in the queue under one indistinguish-
        # able title.
        subject = str(detail.get("title") or detail.get("product_id") or "").strip()
        proposals.append(Proposal(
            module="commerce",
            action_type=action_type,
            target=(subject or target)[:255],
            projected_impact=None if stake is None else round(float(stake), 2),
            baseline=dict(finding.get("baseline") or {}),
            revert_to={"revertible": False},
            note=str(finding.get("reason", ""))[:500],
        ))
    # Measured money first, unknowns after — the order the engine already chose.
    proposals.sort(key=lambda p: (p.projected_impact is None,
                                  -(p.projected_impact or 0.0)))
    return proposals
