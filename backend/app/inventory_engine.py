"""
AI Inventory Planner — deterministic decision engine (module #3).

Same contract as the other engines: ALL math and the recommended action are
computed here with plain, auditable arithmetic — never by an LLM. The LLM layer
only explains the findings.

What it does: from current stock and sales velocity it computes days of cover,
the reorder point, when to reorder, how much to order, and a stockout-risk status
(reorder now / soon / healthy / overstock / no sales).

Everything is expressed in DAYS and UNITS (not absolute dates) so results stay
deterministic and testable; the UI turns days-from-now into a date.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

OVERSTOCK_DAYS = 120          # cover beyond this = capital tied up unnecessarily
DEFAULT_REVIEW_PERIOD = 30    # how often you typically reorder, days
SAFETY_FACTOR = 0.5           # default safety stock = half the lead time


class Status(str, Enum):
    REORDER_NOW = "REORDER_NOW"
    REORDER_SOON = "REORDER_SOON"
    HEALTHY = "HEALTHY"
    OVERSTOCK = "OVERSTOCK"
    NO_SALES = "NO_SALES"


@dataclass
class StockInput:
    name: str
    on_hand: int                       # units available now
    avg_daily_sales: float             # units/day
    lead_time_days: int                # reorder -> arrives at FBA
    inbound: int = 0                   # units already in transit
    review_period_days: int = DEFAULT_REVIEW_PERIOD
    safety_stock_days: int | None = None   # default = ceil(lead_time * SAFETY_FACTOR)
    unit_cost: float = 0.0             # landed cost per unit, $ (for capital figures)


@dataclass
class InventoryFinding:
    name: str
    daily_sales: float
    days_of_cover: float | None        # effective (on hand + inbound); None if no sales
    stockout_in_days: float | None     # on-hand runway; None if no sales
    reorder_point: int                 # units
    days_to_reorder: float | None      # when to place the next order; <=0 means now
    suggested_order_qty: int
    stock_value: float
    order_cost: float
    status: Status
    severity: str                      # info | warning | critical
    reason: str

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d


@dataclass
class InventorySummary:
    total_skus: int
    skus_at_risk: int                  # REORDER_NOW count
    total_stock_value: float
    total_reorder_cost: float          # cost of suggested orders for NOW/SOON
    status_counts: dict[str, int] = field(default_factory=dict)


def _safety_days(s: StockInput) -> int:
    return s.safety_stock_days if s.safety_stock_days is not None else math.ceil(s.lead_time_days * SAFETY_FACTOR)


def analyze_sku(s: StockInput) -> InventoryFinding:
    daily = s.avg_daily_sales
    effective = s.on_hand + s.inbound
    safety = _safety_days(s)
    reorder_point = math.ceil(daily * (s.lead_time_days + safety))
    stock_value = round(effective * s.unit_cost, 2)

    # Target enough stock to cover lead time + one review cycle + safety buffer.
    target_units = daily * (s.lead_time_days + s.review_period_days + safety)
    order_qty = max(0, math.ceil(target_units - effective))
    order_cost = round(order_qty * s.unit_cost, 2)

    if daily <= 0:
        status = Status.NO_SALES
        severity = "warning" if effective > 0 else "info"
        reason = ("No sales velocity — can't forecast. "
                  + (f"{effective} units are sitting as dead stock." if effective > 0 else "No stock, no sales."))
        return InventoryFinding(
            name=s.name, daily_sales=daily, days_of_cover=None, stockout_in_days=None,
            reorder_point=reorder_point, days_to_reorder=None, suggested_order_qty=0,
            stock_value=stock_value, order_cost=0.0, status=status, severity=severity, reason=reason,
        )

    days_of_cover = round(effective / daily, 1)
    stockout_in_days = round(s.on_hand / daily, 1)
    days_to_reorder = round(days_of_cover - (s.lead_time_days + safety), 1)

    if stockout_in_days < s.lead_time_days:
        status, severity = Status.REORDER_NOW, "critical"
        reason = (f"Only {stockout_in_days:.0f} days of stock but lead time is {s.lead_time_days} days — "
                  f"you will stock out before a reorder can arrive. Order {order_qty} units now.")
    elif days_to_reorder <= 0:
        status, severity = Status.REORDER_NOW, "warning"
        reason = (f"At/below the reorder point ({reorder_point} units). "
                  f"Place an order for ~{order_qty} units now to avoid a stockout.")
    elif days_to_reorder <= s.review_period_days:
        status, severity = Status.REORDER_SOON, "info"
        reason = (f"Reorder in about {days_to_reorder:.0f} days (~{order_qty} units). "
                  f"{days_of_cover:.0f} days of cover left.")
    elif days_of_cover > OVERSTOCK_DAYS:
        status, severity = Status.OVERSTOCK, "info"
        reason = (f"{days_of_cover:.0f} days of cover — overstocked; capital is tied up. "
                  f"Slow down ordering and consider a promotion.")
    else:
        status, severity = Status.HEALTHY, "info"
        reason = f"Healthy: {days_of_cover:.0f} days of cover, reorder in ~{days_to_reorder:.0f} days."

    return InventoryFinding(
        name=s.name, daily_sales=daily, days_of_cover=days_of_cover, stockout_in_days=stockout_in_days,
        reorder_point=reorder_point, days_to_reorder=days_to_reorder, suggested_order_qty=order_qty,
        stock_value=stock_value, order_cost=order_cost, status=status, severity=severity, reason=reason,
    )


_SEVERITY_RANK = {"critical": 0, "warning": 1, "info": 2}


def analyze(skus: list[StockInput]) -> tuple[InventorySummary, list[InventoryFinding]]:
    """Analyze SKUs. Returns a summary and findings (most urgent / soonest stockout first)."""
    findings = [analyze_sku(s) for s in skus]

    counts: dict[str, int] = {}
    for f in findings:
        counts[f.status.value] = counts.get(f.status.value, 0) + 1

    summary = InventorySummary(
        total_skus=len(findings),
        skus_at_risk=sum(1 for f in findings if f.status == Status.REORDER_NOW),
        total_stock_value=round(sum(f.stock_value for f in findings), 2),
        total_reorder_cost=round(sum(f.order_cost for f in findings
                                     if f.status in (Status.REORDER_NOW, Status.REORDER_SOON)), 2),
        status_counts=counts,
    )

    def sort_key(f: InventoryFinding):
        soonest = f.stockout_in_days if f.stockout_in_days is not None else float("inf")
        return (_SEVERITY_RANK[f.severity], soonest)

    findings.sort(key=sort_key)
    return summary, findings
