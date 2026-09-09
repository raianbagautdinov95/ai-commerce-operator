"""Tests for the daily report prioritization engine (pure, no DB)."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.report_engine import build_report

INVENTORY = [
    {"name": "Silicone molds", "status": "REORDER_NOW", "severity": "critical",
     "suggested_order_qty": 335, "order_cost": 2177.5, "reason": "stock out soon"},
    {"name": "Dog bowl", "status": "REORDER_SOON", "severity": "info",
     "suggested_order_qty": 75, "order_cost": 337.5, "reason": "reorder soon"},
    {"name": "Yoga mat", "status": "OVERSTOCK", "severity": "info",
     "suggested_order_qty": 0, "order_cost": 0.0, "reason": "overstocked"},
]
PPC = [
    {"keyword": "bakeware set", "action": "NEGATE", "severity": "critical",
     "spend": 25.0, "savings": 25.0, "reason": "wasted"},
    {"keyword": "silicone tray", "action": "LOWER_BID", "severity": "critical",
     "spend": 60.0, "savings": 35.5, "reason": "high acos"},
    {"keyword": "silicone mold", "action": "KEEP", "severity": "info",
     "spend": 40.0, "savings": 0.0, "reason": "healthy"},
]
EVALS = [
    {"name": "Dog bowl", "score": 100, "verdict": "BUY", "economics": {"monthly_profit": 7339.0}},
    {"name": "Garden hose", "score": 38, "verdict": "AVOID", "economics": {"monthly_profit": 1489.0}},
]


def test_filters_and_counts():
    r = build_report(evaluations=EVALS, ppc_findings=PPC, inventory_findings=INVENTORY)
    titles = [i.title for i in r.items]
    # KEEP, OVERSTOCK and AVOID are not actionable -> excluded
    assert not any("KEEP" in t for t in titles)
    assert not any("Yoga mat" in t for t in titles)
    assert not any("Garden hose" in t for t in titles)
    # opportunity (BUY) included
    assert any("Dog bowl" in t and "Opportunity" in t for t in titles)


def test_critical_items_lead():
    r = build_report(evaluations=EVALS, ppc_findings=PPC, inventory_findings=INVENTORY)
    assert r.items[0].severity == "critical"
    # within critical, highest dollar impact first (molds reorder $2177.5 > others)
    assert r.items[0].title.startswith("Reorder Silicone molds")


def test_money_totals():
    r = build_report(evaluations=EVALS, ppc_findings=PPC, inventory_findings=INVENTORY)
    assert r.wasted_spend_usd == 25.0                  # NEGATE spend
    assert r.reorder_cost_usd == round(2177.5 + 337.5, 2)
    assert r.money_at_stake_usd == round(25.0 + 2177.5 + 337.5, 2)


def test_top_n_caps_items():
    r = build_report(evaluations=[], ppc_findings=PPC, inventory_findings=INVENTORY, top_n=2)
    assert len(r.items) == 2


def test_evaluations_deduped_by_name():
    dupes = [
        {"name": "Molds", "score": 100, "verdict": "BUY", "economics": {"monthly_profit": 6000.0}},
        {"name": "Molds", "score": 100, "verdict": "BUY", "economics": {"monthly_profit": 6000.0}},
    ]
    r = build_report(evaluations=dupes, ppc_findings=[], inventory_findings=[])
    assert sum(1 for i in r.items if "Molds" in i.title) == 1


def test_empty_inputs():
    r = build_report(evaluations=[], ppc_findings=[], inventory_findings=[])
    assert r.items == []
    assert r.money_at_stake_usd == 0.0
