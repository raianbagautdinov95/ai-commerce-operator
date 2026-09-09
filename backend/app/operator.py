"""
Operator — orchestrates the modules into one "launch plan" (the 3-agent pipeline).

    Agent 1 (Discover)  -> find the best product opportunity for a niche
    Agent 2 (Suppliers) -> find the best supplier + real landed COGS
    Engine              -> re-score the product with the SOURCED cost (not a guess)
    [Agent 3 (Listing)  -> draft the listing copy — done in the route via the LLM]

This module is the deterministic spine: it sequences the engines and records a
trace of what each agent did. The LLM only drafts the listing text afterwards;
publishing to Amazon is a separate, human-confirmed step (needs SP-API).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from . import decision_engine as de
from . import discovery
from . import suppliers


@dataclass
class Payback:
    """First-order cash picture — how much to put in and when it comes back."""
    first_batch_units: int          # the supplier MOQ
    upfront_investment: float       # units * landed cost (inventory only; excludes ads)
    months_to_sell_batch: float | None
    payback_months: float | None    # upfront / monthly profit
    break_even_units: int | None = None  # units to sell to recoup the investment


@dataclass
class OperatorPlan:
    niche: str
    discovery_source: str
    supplier_source: str
    product: de.Evaluation          # re-scored with the sourced COGS
    inputs: dict                    # product inputs (with sourced cogs) for prefill
    supplier: suppliers.SupplierOffer | None
    suggested_cogs: float | None
    payback: Payback | None = None
    steps: list[str] = field(default_factory=list)


def compute_payback(units: int, landed_cost: float, monthly_sales: float,
                    monthly_profit: float, profit_per_unit: float = 0.0) -> Payback:
    upfront = round(units * landed_cost, 2)
    months_to_sell = round(units / monthly_sales, 1) if monthly_sales > 0 else None
    payback_months = round(upfront / monthly_profit, 1) if monthly_profit > 0 else None
    break_even_units = math.ceil(upfront / profit_per_unit) if profit_per_unit > 0 else None
    return Payback(first_batch_units=units, upfront_investment=upfront,
                   months_to_sell_batch=months_to_sell, payback_months=payback_months,
                   break_even_units=break_even_units)


def plan(niche: str, limit: int = 10, supplier_limit: int = 5, select: str = "score") -> OperatorPlan:
    """Run discover -> source -> re-score and return the assembled plan.

    select="score"  -> the highest-scoring candidate (default).
    select="profit" -> the candidate with the most estimated monthly profit (money-focused).
    """
    steps: list[str] = []

    # Agent 1 — find the best product opportunity.
    dsource, pairs, _ = discovery.discover_detailed(niche, limit)
    if not pairs:
        raise ValueError(f"No product candidates found for '{niche}'.")
    if select == "profit":
        # Pick the biggest earner among WINNABLE options — skip gated AVOIDs and, when
        # possible, mega-brand listings (low competition subscore) you can't realistically beat.
        pool = [pr for pr in pairs if pr[0].verdict != de.Verdict.AVOID] or pairs
        winnable = [pr for pr in pool if pr[0].subscores.get("competition", 0) >= 0.5]
        best_eval, best_input = max(winnable or pool, key=lambda pr: pr[0].economics.monthly_profit)
    else:
        best_eval, best_input = pairs[0]   # discovery returns best-first by score
    steps.append(
        f"Agent 1 (Discover/{dsource}): scanned '{niche}', {len(pairs)} candidates; "
        f"picked '{best_eval.name}' ({best_eval.verdict.value} {best_eval.score}/100, "
        f"~${best_eval.economics.monthly_profit:,.0f}/mo)."
    )

    # Agent 2 — find the best supplier. Source by the generic product type (first few
    # words), not the full branded marketplace title.
    supplier_query = " ".join(best_eval.name.split()[:4])
    ssource, offers, suggested_cogs = suppliers.find_suppliers(supplier_query, supplier_limit)
    supplier = offers[0] if offers else None
    if supplier:
        steps.append(
            f"Agent 2 (Suppliers/{ssource}): best '{supplier.supplier}' ({supplier.country}), "
            f"landed cost ${supplier.est_landed_cost}, MOQ {supplier.moq}."
        )
    else:
        steps.append(f"Agent 2 (Suppliers/{ssource}): no supplier offers found.")

    # Engine — re-score with the cost. Only a REAL supplier source gives a trustworthy
    # cost; the sample source returns a flat placeholder, so for it we keep discovery's
    # proportional COGS estimate (more realistic than the placeholder).
    product = best_eval
    inputs = dict(best_input)
    if suggested_cogs is not None and ssource != "sample":
        inputs["cogs"] = suggested_cogs
        product = de.evaluate(de.ProductInput(**inputs))
        steps.append(
            f"Engine: re-scored with sourced cost ${suggested_cogs} -> "
            f"{product.verdict.value} {product.score}/100."
        )
    else:
        suggested_cogs = round(inputs.get("cogs", 0.0), 2)
        steps.append(
            f"COGS estimated at ${suggested_cogs} (~30% of price) — connect a real "
            f"supplier source for the exact cost."
        )

    # Payback — the first-order cash picture (uses the COGS the verdict is based on).
    units = supplier.moq if supplier else 0
    payback = compute_payback(units, suggested_cogs or 0.0, inputs.get("monthly_sales", 0),
                              product.economics.monthly_profit, product.economics.profit_per_unit)
    if units:
        steps.append(
            f"Payback: ${payback.upfront_investment:,.0f} upfront for {units} units"
            + (f", recouped in ~{payback.payback_months} months." if payback.payback_months else ".")
        )

    return OperatorPlan(
        niche=niche, discovery_source=dsource, supplier_source=ssource,
        product=product, inputs=inputs, supplier=supplier,
        suggested_cogs=suggested_cogs, payback=payback, steps=steps,
    )
