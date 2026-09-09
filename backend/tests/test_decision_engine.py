"""
Tests for the decision engine. Runs with pytest OR standalone (`python test_decision_engine.py`).

The three cases mirror the validated spreadsheet so the code and the sheet agree:
  - Silicone baking molds -> BUY  (~100)
  - Bluetooth headphones  -> AVOID (patent/brand + battery cert gate)
  - Christmas lights      -> CAUTION (seasonal, thinner margin)
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.decision_engine import ProductInput, evaluate, rank, Verdict

MOLDS = ProductInput(
    name="Silicone baking molds", price=27, cogs=6.5, fba_fee=3.30, monthly_sales=600,
    referral_rate=0.15, ppc_per_unit=2.5, size_score=1, dominant_brands=2,
    median_reviews=180, demand_score=1, patent_ok=True, cert_score=1,
)
HEADPHONES = ProductInput(
    name="Bluetooth headphones", price=35, cogs=14, fba_fee=4.98, monthly_sales=400,
    referral_rate=0.08, ppc_per_unit=6, size_score=0.5, dominant_brands=6,
    median_reviews=3500, demand_score=1, patent_ok=False, cert_score=0,
)
GARLAND = ProductInput(
    name="Christmas lights", price=22, cogs=5, fba_fee=4.98, monthly_sales=300,
    referral_rate=0.15, ppc_per_unit=4, size_score=0.5, dominant_brands=4,
    median_reviews=600, demand_score=0, patent_ok=True, cert_score=0.5,
)


def test_molds_buy():
    e = evaluate(MOLDS)
    assert e.verdict == Verdict.BUY
    assert e.score == 100
    assert abs(e.economics.profit_per_unit - 10.53) < 0.01
    assert abs(e.economics.margin - 0.39) < 0.01


def test_headphones_avoid_patent_gate():
    e = evaluate(HEADPHONES)
    assert e.verdict == Verdict.AVOID
    assert "Patent" in e.reason  # hard gate fired despite positive profit


def test_garland_caution():
    e = evaluate(GARLAND)
    assert e.verdict == Verdict.CAUTION
    assert 50 <= e.score < 70


def test_margin_floor_gate():
    thin = ProductInput(name="thin", price=20, cogs=14, fba_fee=3.30,
                        monthly_sales=100, ppc_per_unit=2)
    e = evaluate(thin)
    assert e.verdict == Verdict.AVOID
    assert "Margin" in e.reason


def test_whatif_molds():
    e = evaluate(MOLDS)
    w = e.whatif
    # fixed = 6.5 + 3.42 (fba*1.035) + 2.5 = 12.42 ; rate 0.15
    assert abs(w.break_even_price - 12.42 / 0.85) < 0.02      # ~14.61
    assert abs(w.price_for_floor - 12.42 / 0.70) < 0.02       # ~17.74 (margin 15%)
    assert abs(w.price_for_target - 12.42 / 0.55) < 0.02      # ~22.58 (margin 30%)
    # landed cost can rise from 6.5 to ~12.98 before margin drops below 15%
    assert abs(w.max_cogs_at_floor - 12.98) < 0.05
    assert w.max_cogs_at_floor > MOLDS.cogs                   # current cost has headroom


def test_zero_cogs_full_roi_score():
    # Zero capital with positive profit = unbounded ROI -> full roi points, not 0.
    free = ProductInput(name="freebie", price=25, cogs=0, fba_fee=3.30,
                        monthly_sales=100, ppc_per_unit=2)
    e = evaluate(free)
    assert e.economics.roi == float("inf")
    assert e.subscores["roi"] == 1.0
    assert e.verdict == Verdict.BUY


def test_ranking_order():
    ranked = rank([HEADPHONES, GARLAND, MOLDS])
    assert ranked[0].name == "Silicone baking molds"
    assert ranked[-1].name == "Bluetooth headphones"


def _run_standalone():
    for p in (MOLDS, HEADPHONES, GARLAND):
        e = evaluate(p)
        print(f"{e.name:26} profit=${e.economics.profit_per_unit:6.2f} "
              f"margin={e.economics.margin:5.1%} roi={e.economics.roi:5.0%} "
              f"score={e.score:3d} -> {e.verdict.value} | {e.reason}")
    for fn in [v for k, v in globals().items() if k.startswith("test_")]:
        fn()
    print("\nAll assertions passed.")


if __name__ == "__main__":
    _run_standalone()
