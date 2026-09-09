"""
AI PPC Analyzer — deterministic decision engine (module #2).

Same contract as decision_engine.py: ALL money math and the recommended action
are computed here with plain, auditable arithmetic — never by an LLM. The LLM
layer only explains the findings this module produces.

What it does: given keyword-level Amazon Ads data and the product's break-even
ACOS (= its profit margin before ad cost), it classifies each keyword and
recommends an action — negate wasted spend, lower money-losing bids, scale
winners — and sums up the recoverable spend.

Key terms:
  ACOS            = ad spend / ad sales (lower is better).
  Break-even ACOS = profit margin before ads. ACOS above it loses money.
  Target ACOS     = desired ACOS that still leaves profit (default 70% of break-even).
All figures are orientation values to guide decisions, not guarantees.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

# Tunables (single source of truth).
MIN_CLICKS_FOR_JUDGEMENT = 10   # below this, a no-converting keyword just needs more data
SCALE_ACOS_HEADROOM = 0.75      # acos under target*this = room to scale up
DEFAULT_TARGET_RATIO = 0.70     # target ACOS = break-even * this, when not given
MAX_BID_INCREASE = 1.5          # never suggest raising a bid more than +50%
HIGH_SPEND_CRITICAL = 10.0      # wasted spend at/above this is critical, else warning


class Action(str, Enum):
    NEGATE = "NEGATE"            # clicks but no orders -> add as negative keyword
    LOWER_BID = "LOWER_BID"      # converting but ACOS above break-even -> losing money
    RAISE_BID = "RAISE_BID"      # ACOS well below target -> scale up
    KEEP = "KEEP"                # healthy
    GATHER_DATA = "GATHER_DATA"  # not enough clicks to judge


@dataclass
class KeywordInput:
    keyword: str
    clicks: int
    spend: float
    sales: float
    orders: int
    impressions: int = 0
    match_type: str = "exact"    # exact | phrase | broad


@dataclass
class CampaignInput:
    name: str
    break_even_acos: float                 # = product profit margin (0.35 = 35%)
    keywords: list[KeywordInput]
    target_acos: float | None = None        # desired ACOS; default = break_even * DEFAULT_TARGET_RATIO


@dataclass
class KeywordFinding:
    keyword: str
    match_type: str
    acos: float | None       # None when there are no sales (undefined)
    cpc: float
    cvr: float
    ctr: float
    spend: float
    sales: float
    orders: int
    action: Action
    severity: str            # info | warning | critical
    reason: str
    suggested_bid: float | None
    savings: float           # recoverable spend over the reported period

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["action"] = self.action.value
        return d


@dataclass
class PpcSummary:
    campaign: str
    total_spend: float
    total_sales: float
    overall_acos: float | None
    break_even_acos: float
    target_acos: float
    wasted_spend: float
    potential_savings: float
    action_counts: dict[str, int] = field(default_factory=dict)


def _target_acos(c: CampaignInput) -> float:
    return c.target_acos if c.target_acos is not None else round(c.break_even_acos * DEFAULT_TARGET_RATIO, 4)


def analyze_keyword(k: KeywordInput, break_even: float, target: float) -> KeywordFinding:
    cpc = k.spend / k.clicks if k.clicks else 0.0
    cvr = k.orders / k.clicks if k.clicks else 0.0
    ctr = k.clicks / k.impressions if k.impressions else 0.0
    acos = k.spend / k.sales if k.sales > 0 else None

    suggested_bid: float | None = None
    savings = 0.0

    if k.orders == 0:
        if k.clicks < MIN_CLICKS_FOR_JUDGEMENT:
            action, severity = Action.GATHER_DATA, "info"
            reason = f"Only {k.clicks} clicks and no orders yet — too little data to judge."
        else:
            action = Action.NEGATE
            severity = "critical" if k.spend >= HIGH_SPEND_CRITICAL else "warning"
            savings = round(k.spend, 2)
            reason = f"{k.clicks} clicks, 0 orders, ${k.spend:.2f} spent — wasted; add as a negative keyword."
    else:
        assert acos is not None
        if acos > break_even:
            action, severity = Action.LOWER_BID, "critical"
            suggested_bid = round(cpc * (target / acos), 2)
            savings = round(max(k.spend - k.sales * target, 0.0), 2)
            reason = (f"ACOS {acos:.0%} is above break-even {break_even:.0%} — losing money. "
                      f"Lower the bid toward ~${suggested_bid:.2f} to reach the {target:.0%} target.")
        elif acos <= target * SCALE_ACOS_HEADROOM and k.orders >= 2:
            action, severity = Action.RAISE_BID, "info"
            suggested_bid = round(cpc * min(target / acos, MAX_BID_INCREASE), 2)
            reason = (f"ACOS {acos:.0%} is well below the {target:.0%} target with {k.orders} orders — "
                      f"room to scale. Raise the bid toward ~${suggested_bid:.2f}.")
        else:
            action, severity = Action.KEEP, "info"
            reason = f"ACOS {acos:.0%} is healthy (target {target:.0%}, break-even {break_even:.0%}). Keep as is."

    return KeywordFinding(
        keyword=k.keyword, match_type=k.match_type,
        acos=acos, cpc=round(cpc, 2), cvr=round(cvr, 4), ctr=round(ctr, 4),
        spend=round(k.spend, 2), sales=round(k.sales, 2), orders=k.orders,
        action=action, severity=severity, reason=reason,
        suggested_bid=suggested_bid, savings=savings,
    )


_SEVERITY_RANK = {"critical": 0, "warning": 1, "info": 2}


def analyze(c: CampaignInput) -> tuple[PpcSummary, list[KeywordFinding]]:
    """Analyze a campaign's keywords. Returns a summary and findings (most urgent first)."""
    target = _target_acos(c)
    findings = [analyze_keyword(k, c.break_even_acos, target) for k in c.keywords]

    total_spend = round(sum(f.spend for f in findings), 2)
    total_sales = round(sum(f.sales for f in findings), 2)
    overall_acos = round(total_spend / total_sales, 4) if total_sales > 0 else None
    wasted_spend = round(sum(f.spend for f in findings if f.action == Action.NEGATE), 2)
    potential_savings = round(sum(f.savings for f in findings), 2)

    counts: dict[str, int] = {}
    for f in findings:
        counts[f.action.value] = counts.get(f.action.value, 0) + 1

    summary = PpcSummary(
        campaign=c.name, total_spend=total_spend, total_sales=total_sales,
        overall_acos=overall_acos, break_even_acos=c.break_even_acos, target_acos=target,
        wasted_spend=wasted_spend, potential_savings=potential_savings, action_counts=counts,
    )
    findings.sort(key=lambda f: (_SEVERITY_RANK[f.severity], -f.savings, -f.spend))
    return summary, findings
