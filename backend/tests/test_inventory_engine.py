"""
Tests for the inventory engine. Runs with pytest OR standalone.
The SKUs exercise every status path.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.inventory_engine import StockInput, Status, analyze, analyze_sku

SKUS = [
    StockInput("Fast mover low stock", on_hand=40, avg_daily_sales=5, lead_time_days=30, unit_cost=6.5),
    StockInput("Reorder soon", on_hand=300, avg_daily_sales=5, lead_time_days=30, unit_cost=6.5),
    StockInput("Healthy", on_hand=480, avg_daily_sales=5, lead_time_days=30, unit_cost=6.5),
    StockInput("Overstock", on_hand=1000, avg_daily_sales=5, lead_time_days=30, unit_cost=6.5),
    StockInput("Dead stock", on_hand=100, avg_daily_sales=0, lead_time_days=30, unit_cost=6.5),
]


def _by_name(findings):
    return {f.name: f for f in findings}


def test_statuses():
    _, findings = analyze(SKUS)
    f = _by_name(findings)
    assert f["Fast mover low stock"].status == Status.REORDER_NOW
    assert f["Fast mover low stock"].severity == "critical"
    assert f["Reorder soon"].status == Status.REORDER_SOON
    assert f["Healthy"].status == Status.HEALTHY
    assert f["Overstock"].status == Status.OVERSTOCK
    assert f["Dead stock"].status == Status.NO_SALES


def test_reorder_now_math():
    f = analyze_sku(SKUS[0])
    # daily 5, lead 30, safety ceil(15)=15
    assert f.reorder_point == 5 * (30 + 15)               # 225 units
    assert f.stockout_in_days == 8.0                      # 40 / 5
    # target = 5*(30+30+15)=375; order = ceil(375 - 40) = 335
    assert f.suggested_order_qty == 335
    assert f.order_cost == round(335 * 6.5, 2)


def test_no_sales_has_no_forecast():
    f = analyze_sku(SKUS[4])
    assert f.days_of_cover is None
    assert f.stockout_in_days is None
    assert f.suggested_order_qty == 0
    assert f.severity == "warning"  # dead stock on hand


def test_summary_and_ordering():
    summary, findings = analyze(SKUS)
    assert summary.total_skus == 5
    assert summary.skus_at_risk == 1                      # one REORDER_NOW
    assert summary.status_counts["REORDER_NOW"] == 1
    assert summary.total_stock_value > 0
    # most urgent first
    assert findings[0].severity == "critical"


def test_inbound_extends_cover():
    base = StockInput("x", on_hand=40, avg_daily_sales=5, lead_time_days=30)
    withteam = StockInput("x", on_hand=40, avg_daily_sales=5, lead_time_days=30, inbound=400)
    assert analyze_sku(withteam).days_of_cover > analyze_sku(base).days_of_cover


if __name__ == "__main__":
    summary, findings = analyze(SKUS)
    print(f"SKUs {summary.total_skus} | at risk {summary.skus_at_risk} | "
          f"stock value ${summary.total_stock_value} | reorder cost ${summary.total_reorder_cost}")
    for f in findings:
        doc = "n/a" if f.days_of_cover is None else f"{f.days_of_cover:.0f}d"
        print(f"  [{f.severity:8}] {f.status.value:12} {f.name:22} cover {doc:>5} "
              f"order {f.suggested_order_qty}")
    for fn in [v for k, v in globals().items() if k.startswith("test_")]:
        fn()
    print("\nAll assertions passed.")
