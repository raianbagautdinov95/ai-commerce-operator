"""
Autopilot scanning, independent of the web layer.

These two functions are used by both the API and the RQ worker. They live here
rather than in `main.py` so that starting a worker does not import the entire
FastAPI application — a worker has no business constructing routes, middleware
and an OpenAPI schema just to score a niche.

Neither function decides anything about money on its own: `plan()` runs the
deterministic pipeline and `_meets_money_criteria` is a plain comparison against
thresholds the caller supplied.
"""
from __future__ import annotations

from . import llm, operator
from .schemas import AutopilotScanRequest


def operator_to_detail(niche: str, limit: int) -> dict:
    """Run the 3-agent pipeline for a niche and shape it into a queue-storable plan."""
    p = operator.plan(niche, limit, select="profit")   # money-focused: the biggest earner
    eco = p.product.economics
    attrs = (f"Selling price: ${p.inputs.get('price')}. Margin: {eco.margin:.0%}. "
             f"Small/light item: {p.inputs.get('size_score', 1) >= 1}.")
    listing = llm.draft_listing(p.product.name, attrs)
    roi = eco.roi if eco.roi != float("inf") else 999.0
    return {
        "status": "pending",
        "niche": niche,
        "severity": "info" if p.product.verdict.value == "BUY" else "warning",
        "product": {
            "name": p.product.name,
            "verdict": p.product.verdict.value,
            "score": p.product.score,
            "margin": eco.margin,
            "roi": roi,
            "competition": p.product.subscores.get("competition", 0.0),
            "monthly_profit": eco.monthly_profit,
            "inputs": p.inputs,
        },
        "supplier": p.supplier.supplier if p.supplier else None,
        "suggested_cogs": p.suggested_cogs,
        "payback": p.payback.__dict__ if p.payback else None,
        "listing_draft": listing,
        "steps": p.steps,
    }


def meets_money_criteria(detail: dict, req: AutopilotScanRequest) -> bool:
    """Every filter a seller can put on what the autopilot is allowed to queue."""
    product = detail["product"]
    payback = detail.get("payback") or {}
    if req.only_buy and product.get("verdict") != "BUY":
        return False
    if req.min_margin is not None and product.get("margin", 0) < req.min_margin:
        return False
    if req.min_monthly_profit is not None and product.get("monthly_profit", 0) < req.min_monthly_profit:
        return False
    if req.min_roi is not None and product.get("roi", 0) < req.min_roi:
        return False
    if req.min_competition is not None and product.get("competition", 0) < req.min_competition:
        return False
    if req.max_payback_months is not None:
        months = payback.get("payback_months")
        if months is None or months > req.max_payback_months:
            return False
    return True
