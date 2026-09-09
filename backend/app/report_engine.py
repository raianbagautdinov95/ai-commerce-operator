"""
Daily Report — deterministic prioritization across modules (the "AI COO" digest).

Pulls the latest findings from every module (product hunter, PPC, inventory) and
ranks them into one prioritized to-do list by severity then dollar impact. As with
every engine, the ranking and the money are computed here; the LLM only writes the
narrative briefing on top.

Pure function: it takes already-extracted finding dicts (the route reads them from
the DB), so it stays deterministic and testable.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

_SEVERITY_RANK = {"critical": 0, "warning": 1, "info": 2}


@dataclass
class ReportItem:
    module: str          # product_hunter | ppc | inventory
    severity: str        # critical | warning | info
    action: str          # short action label
    title: str
    impact_usd: float    # dollars at stake / recoverable / at opportunity
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DailyReport:
    items: list[ReportItem]
    money_at_stake_usd: float       # wasted spend + reorder cost (protect/recover)
    wasted_spend_usd: float
    reorder_cost_usd: float
    counts: dict[str, int] = field(default_factory=dict)   # by severity


def _from_inventory(findings: list[dict]) -> list[ReportItem]:
    items: list[ReportItem] = []
    for f in findings:
        if f.get("status") in ("REORDER_NOW", "REORDER_SOON"):
            qty = f.get("suggested_order_qty", 0)
            items.append(ReportItem(
                module="inventory", severity=f.get("severity", "info"),
                action=f.get("status", ""),
                title=f"Reorder {f.get('name', '?')} ({qty} units)",
                impact_usd=round(float(f.get("order_cost", 0.0)), 2),
                detail=f.get("reason", ""),
            ))
    return items


def _from_ppc(findings: list[dict]) -> list[ReportItem]:
    items: list[ReportItem] = []
    for f in findings:
        action = f.get("action")
        if action == "NEGATE":
            impact = float(f.get("spend", 0.0))
        elif action == "LOWER_BID":
            impact = float(f.get("savings", 0.0))
        else:
            continue
        items.append(ReportItem(
            module="ppc", severity=f.get("severity", "info"), action=action,
            title=f"{action} '{f.get('keyword', '?')}'",
            impact_usd=round(impact, 2), detail=f.get("reason", ""),
        ))
    return items


def _from_evaluations(evaluations: list[dict]) -> list[ReportItem]:
    items: list[ReportItem] = []
    seen: set[str] = set()
    for e in evaluations:
        if e.get("verdict") != "BUY":
            continue
        name = e.get("name", "?")
        if name in seen:   # evaluations arrive newest-first; keep only the latest per product
            continue
        seen.add(name)
        monthly = float((e.get("economics") or {}).get("monthly_profit", 0.0))
        items.append(ReportItem(
            module="product_hunter", severity="info", action="OPPORTUNITY",
            title=f"Opportunity: {e.get('name', '?')} (score {e.get('score', '?')})",
            impact_usd=round(monthly, 2),
            detail=f"BUY candidate — ~${monthly:,.0f}/mo potential profit.",
        ))
    return items


def build_report(
    *,
    evaluations: list[dict],
    ppc_findings: list[dict],
    inventory_findings: list[dict],
    top_n: int = 12,
) -> DailyReport:
    items = _from_inventory(inventory_findings) + _from_ppc(ppc_findings) + _from_evaluations(evaluations)
    items.sort(key=lambda i: (_SEVERITY_RANK.get(i.severity, 3), -i.impact_usd))

    wasted = round(sum(i.impact_usd for i in items if i.module == "ppc" and i.action == "NEGATE"), 2)
    reorder = round(sum(i.impact_usd for i in items if i.module == "inventory"), 2)

    counts: dict[str, int] = {}
    for i in items:
        counts[i.severity] = counts.get(i.severity, 0) + 1

    return DailyReport(
        items=items[:top_n],
        money_at_stake_usd=round(wasted + reorder, 2),
        wasted_spend_usd=wasted,
        reorder_cost_usd=reorder,
        counts=counts,
    )
