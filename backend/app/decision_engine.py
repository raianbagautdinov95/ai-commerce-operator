"""
AI Product Hunter — deterministic decision engine.

This is the CORE of the product and the long-term moat. All money math and the
verdict are computed here with plain, auditable arithmetic — NOT by an LLM.
The LLM layer (see app/llm.py) only *explains* the numbers this module produces.

The scoring rubric and hard gates mirror the validated spreadsheet
(Amazon_Product_Picker.xlsx) so results stay consistent across tools.

Amazon fee orientation (US, 2026):
  - Referral fee = max(price * rate, $0.30); rate 15% most categories, 8% for
    electronics/beauty over $10.
  - FBA fulfillment fee orientation: small std $3.30, large std $4.98,
    small oversize $8.74, large oversize $75.86.
  - +3.5% fuel & logistics surcharge applied on the FBA fee.
Always verify the exact FBA fee in Amazon's official FBA Revenue Calculator.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any

FBA_FUEL_SURCHARGE = 0.035
MIN_REFERRAL_FEE = 0.30

# Scoring weights (sum = 100). Single source of truth.
WEIGHTS = {
    "margin": 25,
    "roi": 15,
    "price": 10,
    "size": 12,
    "competition": 20,
    "demand": 8,
    "patent": 5,
    "certification": 5,
}

# Decision thresholds
MARGIN_TARGET = 0.30          # margin that earns full margin points
ROI_TARGET = 1.00            # ROI (100%) that earns full roi points
MARGIN_HARD_FLOOR = 0.15     # below this -> AVOID regardless of score
BUY_THRESHOLD = 70
CAUTION_THRESHOLD = 50


class Verdict(str, Enum):
    BUY = "BUY"
    CAUTION = "CAUTION"
    AVOID = "AVOID"


@dataclass
class ProductInput:
    """Everything needed to evaluate one product candidate."""
    name: str
    price: float                 # selling price, $
    cogs: float                  # landed cost per unit (supplier + freight to FBA), $
    fba_fee: float               # FBA fulfillment fee, $ (from Amazon calculator)
    monthly_sales: float         # estimated units / month
    referral_rate: float = 0.15  # Amazon referral fee rate (0.15 = 15%)
    ppc_per_unit: float = 0.0    # ad + other variable cost per unit, $
    size_score: float = 1.0      # 1 small/light, 0.5 medium, 0 oversize
    dominant_brands: int = 0     # number of dominant brands in top-10
    median_reviews: int = 0      # median review count of top-10
    demand_score: float = 1.0    # 1 year-round, 0.5 mildly seasonal, 0 very seasonal
    patent_ok: bool = True       # True = no patent/brand risk
    cert_score: float = 1.0      # 1 simple/none, 0.5 medium, 0 hard (batteries, cosmetics, kids, food)


@dataclass
class Economics:
    referral_fee: float
    fba_fee_total: float
    profit_per_unit: float
    margin: float
    roi: float
    monthly_profit: float


@dataclass
class WhatIf:
    """Deterministic sensitivity analysis — the safety cushion around a decision."""
    break_even_price: float | None       # price where profit per unit hits $0
    price_for_floor: float | None        # price where margin == MARGIN_HARD_FLOOR (15%)
    price_for_target: float | None       # price where margin == MARGIN_TARGET (30%)
    max_cogs_at_floor: float             # highest landed cost (at current price) keeping margin >= floor


@dataclass
class Evaluation:
    name: str
    economics: Economics
    score: int
    subscores: dict[str, float]
    verdict: Verdict
    reason: str
    whatif: WhatIf
    pros: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["verdict"] = self.verdict.value
        return d


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def compute_economics(p: ProductInput) -> Economics:
    referral_fee = max(p.price * p.referral_rate, MIN_REFERRAL_FEE)
    fba_fee_total = p.fba_fee * (1 + FBA_FUEL_SURCHARGE)
    profit = p.price - p.cogs - referral_fee - fba_fee_total - p.ppc_per_unit
    margin = profit / p.price if p.price else 0.0
    # ROI = profit / capital. Zero capital with positive profit is unbounded
    # (the best possible case), so it earns full roi points — not 0, which would
    # wrongly penalise it. No profit at zero cost stays neutral.
    if p.cogs > 0:
        roi = profit / p.cogs
    elif profit > 0:
        roi = float("inf")
    else:
        roi = 0.0
    return Economics(
        referral_fee=round(referral_fee, 2),
        fba_fee_total=round(fba_fee_total, 2),
        profit_per_unit=round(profit, 2),
        margin=margin,
        roi=roi,
        monthly_profit=round(profit * p.monthly_sales, 2),
    )


def compute_whatif(p: ProductInput, eco: Economics) -> WhatIf:
    """How far the inputs can move before the economics break.

    Price thresholds assume the referral fee is in its percentage regime
    (price * rate, not the $0.30 floor) — true for essentially all real prices —
    so they are orientation values, like the rest of the engine.
    """
    fixed = p.cogs + eco.fba_fee_total + p.ppc_per_unit  # costs independent of price
    rate = p.referral_rate

    def price_for_margin(m: float) -> float | None:
        denom = 1 - rate - m
        return round(fixed / denom, 2) if denom > 0 else None

    break_even = round(fixed / (1 - rate), 2) if rate < 1 else None
    # At the current price/fees, how high can landed cost go before margin < floor:
    max_cogs = p.price * (1 - MARGIN_HARD_FLOOR) - eco.referral_fee - eco.fba_fee_total - p.ppc_per_unit
    return WhatIf(
        break_even_price=break_even,
        price_for_floor=price_for_margin(MARGIN_HARD_FLOOR),
        price_for_target=price_for_margin(MARGIN_TARGET),
        max_cogs_at_floor=round(max(max_cogs, 0.0), 2),
    )


def _competition_subscore(p: ProductInput) -> float:
    brands = 1.0 if p.dominant_brands <= 2 else 0.5 if p.dominant_brands <= 4 else 0.0
    reviews = 1.0 if p.median_reviews <= 200 else 0.5 if p.median_reviews <= 750 else 0.0
    return (brands + reviews) / 2


def _price_subscore(price: float) -> float:
    if 20 <= price <= 50:
        return 1.0
    if price < 15 or price > 70:
        return 0.0
    return 0.5


def compute_subscores(p: ProductInput, eco: Economics) -> dict[str, float]:
    return {
        "margin": _clamp(eco.margin / MARGIN_TARGET),
        "roi": _clamp(eco.roi / ROI_TARGET),
        "price": _price_subscore(p.price),
        "size": _clamp(p.size_score),
        "competition": _competition_subscore(p),
        "demand": _clamp(p.demand_score),
        "patent": 1.0 if p.patent_ok else 0.0,
        "certification": _clamp(p.cert_score),
    }


def compute_score(subscores: dict[str, float]) -> int:
    return round(sum(WEIGHTS[k] * subscores[k] for k in WEIGHTS))


def decide(eco: Economics, score: int, p: ProductInput) -> tuple[Verdict, str]:
    # Hard gates override the score.
    if not p.patent_ok:
        return Verdict.AVOID, "Patent / brand risk — legal exposure outweighs any upside."
    if eco.margin < MARGIN_HARD_FLOOR:
        return Verdict.AVOID, f"Margin {eco.margin:.0%} below the {MARGIN_HARD_FLOOR:.0%} floor — too thin to survive ads and returns."
    if score >= BUY_THRESHOLD:
        return Verdict.BUY, f"Strong score ({score}/100): healthy economics and manageable competition."
    if score >= CAUTION_THRESHOLD:
        return Verdict.CAUTION, f"Mixed score ({score}/100): workable but with notable weak spots — proceed carefully."
    return Verdict.AVOID, f"Low score ({score}/100): the economics or competition make this a poor bet."


def _build_pros_risks(p: ProductInput, eco: Economics, sub: dict[str, float]) -> tuple[list[str], list[str]]:
    pros, risks = [], []
    if eco.margin >= MARGIN_TARGET:
        pros.append(f"High margin ({eco.margin:.0%}) leaves room for ads and mistakes.")
    if eco.roi >= ROI_TARGET:
        pros.append(f"Fast capital turnover (ROI {eco.roi:.0%}).")
    if sub["size"] >= 1.0:
        pros.append("Small and light — cheap FBA fees and logistics.")
    if sub["competition"] >= 1.0:
        pros.append("Low competition — few dominant brands and beatable review counts.")
    if sub["demand"] >= 1.0:
        pros.append("Steady year-round demand.")

    if eco.margin < MARGIN_TARGET:
        risks.append(f"Margin ({eco.margin:.0%}) is below the {MARGIN_TARGET:.0%} comfort target.")
    if not p.patent_ok:
        risks.append("Patent / brand risk — verify on Google Patents before sourcing.")
    if p.dominant_brands > 2 or p.median_reviews > 200:
        risks.append("Competition is entrenched — strong listing and reviews needed to break in.")
    if p.demand_score < 1.0:
        risks.append("Demand is seasonal — sales may dip outside peak months.")
    if p.cert_score < 1.0:
        risks.append("Certification/compliance is non-trivial (e.g. batteries, cosmetics, kids, food).")
    if p.size_score < 1.0:
        risks.append("Larger/heavier item — higher FBA and shipping costs.")
    return pros, risks


def evaluate(p: ProductInput) -> Evaluation:
    """Full evaluation of a single product candidate."""
    eco = compute_economics(p)
    sub = compute_subscores(p, eco)
    score = compute_score(sub)
    verdict, reason = decide(eco, score, p)
    pros, risks = _build_pros_risks(p, eco, sub)
    whatif = compute_whatif(p, eco)
    return Evaluation(
        name=p.name, economics=eco, score=score, subscores=sub,
        verdict=verdict, reason=reason, whatif=whatif, pros=pros, risks=risks,
    )


def rank(products: list[ProductInput]) -> list[Evaluation]:
    """Evaluate and rank candidates best-first. AVOID-by-gate sinks to the bottom."""
    evals = [evaluate(p) for p in products]
    order = {Verdict.BUY: 0, Verdict.CAUTION: 1, Verdict.AVOID: 2}
    return sorted(evals, key=lambda e: (order[e.verdict], -e.score))
