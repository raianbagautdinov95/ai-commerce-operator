"""
Tests for the PPC engine. Runs with pytest OR standalone.
A representative campaign exercises every action path.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.ppc_engine import (Action, CampaignInput, KeywordInput, analyze,
                            analyze_keyword)

CAMPAIGN = CampaignInput(
    name="Silicone molds — auto",
    break_even_acos=0.35,          # product margin 35% -> break-even ACOS 35%
    keywords=[
        KeywordInput("silicone mold", clicks=50, spend=40, sales=200, orders=8),     # healthy -> KEEP
        KeywordInput("bakeware set", clicks=30, spend=25, sales=0, orders=0),         # wasted -> NEGATE (critical)
        KeywordInput("cake", clicks=5, spend=3, sales=0, orders=0),                   # too little -> GATHER_DATA
        KeywordInput("silicone tray", clicks=40, spend=60, sales=100, orders=4),      # ACOS 60% -> LOWER_BID
        KeywordInput("molds cheap", clicks=60, spend=20, sales=300, orders=15),       # ACOS 6.7% -> RAISE_BID
    ],
)


def _by_kw(findings):
    return {f.keyword: f for f in findings}


def test_target_acos_default():
    summary, _ = analyze(CAMPAIGN)
    assert abs(summary.target_acos - 0.245) < 1e-6   # 0.35 * 0.70


def test_actions_per_keyword():
    _, findings = analyze(CAMPAIGN)
    f = _by_kw(findings)
    assert f["silicone mold"].action == Action.KEEP
    assert f["bakeware set"].action == Action.NEGATE
    assert f["bakeware set"].severity == "critical"
    assert f["cake"].action == Action.GATHER_DATA
    assert f["silicone tray"].action == Action.LOWER_BID
    assert f["silicone tray"].severity == "critical"
    assert f["molds cheap"].action == Action.RAISE_BID


def test_negate_savings_and_wasted_spend():
    summary, findings = analyze(CAMPAIGN)
    f = _by_kw(findings)
    assert f["bakeware set"].savings == 25.0
    assert summary.wasted_spend == 25.0


def test_lower_bid_math():
    _, findings = analyze(CAMPAIGN)
    f = _by_kw(findings)["silicone tray"]
    # acos = 60/100 = 0.60 ; cpc = 60/40 = 1.5 ; target 0.245
    assert abs(f.acos - 0.60) < 1e-9
    assert f.suggested_bid == round(1.5 * (0.245 / 0.60), 2)   # ~0.61, lower than cpc
    assert f.suggested_bid < f.cpc
    assert f.savings == round(60 - 100 * 0.245, 2)             # 35.5


def test_raise_bid_capped():
    _, findings = analyze(CAMPAIGN)
    f = _by_kw(findings)["molds cheap"]
    # target/acos huge, so capped at +50% of the true cpc (20/60)
    assert f.suggested_bid == round((20 / 60) * 1.5, 2)   # 0.50
    assert f.suggested_bid > f.cpc


def test_summary_totals_and_ordering():
    summary, findings = analyze(CAMPAIGN)
    assert summary.total_spend == 148.0
    assert summary.total_sales == 600.0
    assert summary.overall_acos == round(148 / 600, 4)
    assert summary.potential_savings == round(25 + 35.5, 2)
    # most urgent first: critical findings lead
    assert findings[0].severity == "critical"


def test_no_sales_acos_is_none():
    f = analyze_keyword(KeywordInput("x", clicks=20, spend=8, sales=0, orders=0), 0.35, 0.245)
    assert f.acos is None
    assert f.action == Action.NEGATE


if __name__ == "__main__":
    summary, findings = analyze(CAMPAIGN)
    print(f"Campaign: {summary.campaign}")
    print(f"Spend ${summary.total_spend} | Sales ${summary.total_sales} | "
          f"ACOS {summary.overall_acos:.0%} | wasted ${summary.wasted_spend} | "
          f"savings ${summary.potential_savings}")
    for f in findings:
        acos = "n/a" if f.acos is None else f"{f.acos:.0%}"
        bid = "" if f.suggested_bid is None else f" -> bid ${f.suggested_bid}"
        print(f"  [{f.severity:8}] {f.action.value:11} {f.keyword:16} ACOS {acos:>4}{bid}")
    for fn in [v for k, v in globals().items() if k.startswith("test_")]:
        fn()
    print("\nAll assertions passed.")
