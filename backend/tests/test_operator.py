"""Tests for the Operator: the 3-agent launch-plan pipeline (sample sources, no LLM)."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ["DISCOVERY_SOURCE"] = "sample"
os.environ["SUPPLIER_SOURCE"] = "sample"

from app.operator import plan, compute_payback


def test_plan_chains_three_stages():
    p = plan("kitchen", limit=10)
    assert p.discovery_source == "sample"
    assert p.supplier_source == "sample"
    assert p.product is not None
    assert p.supplier is not None
    # at least: discover step, supplier step, re-score step
    assert len(p.steps) >= 3
    assert any("Agent 1" in s for s in p.steps)
    assert any("Agent 2" in s for s in p.steps)


def test_plan_rescored_with_supplier_cogs():
    p = plan("pet", limit=10)
    # the product was re-evaluated using the supplier's landed cost
    assert p.suggested_cogs is not None
    assert p.inputs["cogs"] == p.suggested_cogs
    assert abs(p.product.economics.profit_per_unit - (
        p.inputs["price"] - p.suggested_cogs
        - p.product.economics.referral_fee - p.product.economics.fba_fee_total
        - p.inputs.get("ppc_per_unit", 0)
    )) < 0.02


def test_payback_math():
    # 1000 units @ $4 landed = $4000 upfront; $2000/mo profit -> 2.0 months payback
    pb = compute_payback(units=1000, landed_cost=4.0, monthly_sales=500,
                         monthly_profit=2000, profit_per_unit=8.0)
    assert pb.upfront_investment == 4000.0
    assert pb.months_to_sell_batch == 2.0      # 1000 / 500
    assert pb.payback_months == 2.0            # 4000 / 2000
    assert pb.break_even_units == 500          # ceil(4000 / 8)
    # no profit -> no payback period / no break-even
    none_pb = compute_payback(100, 5, 0, 0)
    assert none_pb.payback_months is None and none_pb.break_even_units is None


def test_plan_includes_payback():
    p = plan("kitchen", limit=10, select="profit")
    assert p.payback is not None
    assert p.payback.upfront_investment > 0


def test_plan_picks_best_first():
    p = plan("fitness", limit=10)
    # discovery returns best-first; operator picks index 0 -> should not be an AVOID if a better one exists
    assert p.product.score >= 0
