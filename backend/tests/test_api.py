"""
API integration tests — exercise the FastAPI routes end to end against an
isolated in-memory SQLite DB. The LLM is forced to 'none' so explanations use the
deterministic template (no network, no cost).
"""
import os
import sys
import datetime as dt
from decimal import Decimal

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Force template explanations and offline data sources before anything reads them.
os.environ["LLM_PROVIDER"] = "none"
os.environ["DISCOVERY_SOURCE"] = "sample"
os.environ["SUPPLIER_SOURCE"] = "sample"

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.models import AmazonSalesDaily, Base
from app.db import crud, models
from app.db.session import get_session
from app.main import app
from app.main import _validate_runtime_safety

# One shared in-memory DB for the whole module (StaticPool keeps it alive).
_engine = create_engine(
    "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
)
Base.metadata.create_all(_engine)
_TestSession = sessionmaker(bind=_engine, expire_on_commit=False)


def _override_session():
    db = _TestSession()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_session] = _override_session
client = TestClient(app)


def test_readiness_rejects_traffic_without_required_workers(monkeypatch):
    from app import queueing

    monkeypatch.setenv("QUEUE_ENABLED", "true")
    monkeypatch.setattr(queueing, "redis_ready", lambda: True)
    monkeypatch.setattr(queueing, "workers_ready", lambda: False)

    response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json() == {"detail": "service not ready"}


def test_readiness_accepts_traffic_with_required_workers(monkeypatch):
    from app import queueing

    monkeypatch.setenv("QUEUE_ENABLED", "true")
    monkeypatch.setattr(queueing, "redis_ready", lambda: True)
    monkeypatch.setattr(queueing, "workers_ready", lambda: True)

    response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_enqueue_fails_fast_when_queue_has_no_worker(monkeypatch):
    from app import queueing

    monkeypatch.setattr(queueing, "workers_ready", lambda required: False)
    with pytest.raises(RuntimeError, match="integrations"):
        queueing.enqueue_shopify_sync(
            job_id="job-1", tenant_id="tenant-1", actor_id="actor-1",
            channel_id="channel-1", days=30,
        )


def test_job_status_uses_dev_tenant_when_auth_is_disabled(monkeypatch):
    from app import queueing

    db = _TestSession()
    try:
        expected_tenant = str(crud.get_or_create_dev_store(db).id)
    finally:
        db.close()

    monkeypatch.setenv("QUEUE_ENABLED", "true")

    def fake_status(job_id: str, *, tenant_id: str):
        assert tenant_id == expected_tenant
        return {"job_id": job_id, "status": "finished", "result": {"orders": 1}}

    monkeypatch.setattr(queueing, "job_status", fake_status)
    response = client.get("/api/jobs/test-job")
    assert response.status_code == 200
    assert response.json() == {
        "job_id": "test-job", "status": "finished", "result": {"orders": 1}
    }


def test_roi_dashboard_uses_actual_tenant_sales_and_keeps_profit_unknown():
    db = _TestSession()
    store = crud.get_or_create_dev_store(db)
    today = dt.datetime.now(dt.timezone.utc).date()
    now = dt.datetime.now(dt.timezone.utc)
    db.add_all([
        AmazonSalesDaily(
            store_id=store.id, marketplace_id="ROI-A1", sales_date=today - dt.timedelta(days=1),
            ordered_sales=Decimal("120.00"), currency="EUR", units_ordered=6,
            order_items=4, page_views=60, sessions=40, synced_at=now,
        ),
        AmazonSalesDaily(
            store_id=store.id, marketplace_id="ROI-A1", sales_date=today - dt.timedelta(days=31),
            ordered_sales=Decimal("80.00"), currency="EUR", units_ordered=4,
            order_items=2, page_views=40, sessions=20, synced_at=now,
        ),
    ])
    db.commit()
    response = client.get("/api/dashboard/roi?marketplace_id=ROI-A1&days=30")
    assert response.status_code == 200
    result = response.json()
    assert result["revenue"] == 120.0
    assert result["previous_revenue"] == 80.0
    assert result["revenue_change_percentage"] == 50.0
    assert result["conversion_rate"] == 10.0
    assert result["average_order_value"] == 30.0
    assert result["freshness"] == "fresh"
    assert result["profit"] is None and result["roi"] is None
    assert set(result["missing_profit_inputs"]) == {
        "landed_cogs", "amazon_fees", "advertising_spend"
    }


def test_sku_cost_catalog_is_idempotent_and_tenant_scoped():
    payload = {"marketplace_id": "COST-A1", "costs": [
        {"sku": "SKU-1", "landed_cost": 6.25, "currency": "eur"}
    ]}
    headers = {"Idempotency-Key": "cost-catalog-1"}
    first = client.put("/api/integrations/amazon/costs", json=payload, headers=headers)
    second = client.put("/api/integrations/amazon/costs", json=payload, headers=headers)
    assert first.status_code == second.status_code == 200
    assert first.json()["results"][0]["landed_cost"] == 6.25
    assert first.json()["results"][0]["currency"] == "EUR"
    listed = client.get("/api/integrations/amazon/costs?marketplace_id=COST-A1")
    assert len(listed.json()["results"]) == 1


def test_demo_seed_is_labelled_complete_and_resettable():
    first = client.post("/api/demo/seed", headers={"Idempotency-Key": "demo-seed-1"})
    assert first.status_code == 200
    assert first.json() == {
        "marketplace_id": "DEMO-A1", "days": 60, "skus": 3,
        "message": "Synthetic demo data is ready.",
    }
    roi = client.get("/api/dashboard/roi?marketplace_id=DEMO-A1&days=30")
    assert roi.status_code == 200
    result = roi.json()
    assert result["demo_data"] is True
    assert result["revenue"] > 0
    assert result["amazon_fees"] > 0
    assert result["advertising_spend"] > 0
    assert result["landed_cogs"] > 0
    assert result["cogs_coverage_percentage"] == 100.0
    assert result["missing_profit_inputs"] == []
    assert result["profit"] > 0
    assert result["margin"] > 0
    assert result["roi"] > 0
    commerce = client.get("/api/dashboard/commerce?days=30")
    assert commerce.status_code == 200
    commerce_result = commerce.json()
    assert commerce_result["demo_data"] is True
    assert commerce_result["revenue"] > 0
    assert commerce_result["profit"] > 0
    assert {channel["provider"] for channel in commerce_result["channels"]} == {
        "amazon", "shopify", "woocommerce"
    }
    assert round(sum(channel["share_percentage"] for channel in
                     commerce_result["channels"]), 0) == 100

    reset = client.post("/api/demo/seed", headers={"Idempotency-Key": "demo-seed-2"})
    assert reset.status_code == 200
    db = _TestSession()
    assert db.query(AmazonSalesDaily).filter_by(marketplace_id="DEMO-A1").count() == 60
    assert db.query(models.ChannelConnection).filter(
        models.ChannelConnection.external_account_id.like("DEMO-%")
    ).count() == 3
    assert db.query(models.CommerceDailyMetric).count() == 180


def test_commerce_dashboard_never_mixes_demo_with_real_channels():
    db = _TestSession()
    store = crud.get_or_create_dev_store(db)
    real_channel = models.ChannelConnection(
        store_id=store.id, provider="shopify",
        external_account_id="real-dashboard-test.myshopify.com",
        display_name="Real dashboard test", status="connected", currency="EUR",
        settings={"permissions": "read"},
    )
    db.add(real_channel); db.flush()
    db.add(models.CommerceDailyMetric(
        store_id=store.id, channel_id=real_channel.id,
        metric_date=dt.datetime.now(dt.timezone.utc).date(),
        revenue=Decimal("42.00"), refunds=0, fees=0, landed_cogs=0,
        advertising_spend=0, orders=2, units=3, sessions=0,
        currency="EUR", source="shopify_graphql", costs_complete=False,
    ))
    db.commit(); db.close()

    response = client.get("/api/dashboard/commerce?days=30")
    assert response.status_code == 200
    result = response.json()
    assert result["demo_data"] is False
    assert result["revenue"] == 42.0
    assert result["orders"] == 2
    assert [channel["display_name"] for channel in result["channels"]] == [
        "Real dashboard test"
    ]


def test_commerce_dashboard_returns_one_row_per_day_for_the_chart():
    """The chart draws this series, and it used to have none to draw.

    A connected Shopify store showed a real revenue total beside an empty chart
    telling the seller to connect a channel, because the only day-by-day series
    the API offered came from the Amazon tables. Two channels selling on the
    same date must fold into one point, or the line doubles back on itself.
    """
    db = _TestSession()
    store = crud.get_or_create_dev_store(db)
    day = dt.datetime.now(dt.timezone.utc).date()
    made = []
    for name in ("chart-a", "chart-b"):
        channel = models.ChannelConnection(
            store_id=store.id, provider="shopify",
            external_account_id=f"{name}.myshopify.com", display_name=name,
            status="connected", currency="EUR", settings={},
        )
        db.add(channel); db.flush(); made.append(channel)
    for channel in made:
        db.add(models.CommerceDailyMetric(
            store_id=store.id, channel_id=channel.id, metric_date=day,
            revenue=Decimal("5.00"), refunds=0, fees=0, landed_cogs=0,
            advertising_spend=0, orders=1, units=1, sessions=0,
            currency="EUR", source="shopify_graphql", costs_complete=False,
        ))
    db.commit(); db.close()

    result = client.get("/api/dashboard/commerce?days=30").json()
    dates = [row["date"] for row in result["daily"]]
    assert dates, "a connected store with sales must have a series to draw"
    assert len(dates) == len(set(dates)), "the same day must not appear twice"
    assert dates == sorted(dates), "oldest first, or the line runs backwards"
    today = [row for row in result["daily"] if row["date"] == day.isoformat()]
    assert len(today) == 1
    assert today[0]["revenue"] >= 10.0, "both channels' sales belong to the day"


def test_commerce_dashboard_fills_in_the_days_that_sold_nothing():
    """A quiet day is a real zero, and leaving it out of the series made the
    chart lie twice.

    It drew revenue straight across a fortnight in which nobody bought anything,
    and the trend fitted through those points read one row as one day — so a
    twelve-day gap counted as a single step, the slope came out roughly twelve
    times too steep, and a store that had earned $48 was shown projecting $234.
    """
    db = _TestSession()
    store = crud.get_or_create_dev_store(db)
    channel = models.ChannelConnection(
        store_id=store.id, provider="shopify",
        external_account_id="gap-shop.myshopify.com", display_name="gap-shop",
        status="connected", currency="EUR", settings={},
    )
    db.add(channel); db.flush()
    old = dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=6)
    db.add(models.CommerceDailyMetric(
        store_id=store.id, channel_id=channel.id, metric_date=old,
        revenue=Decimal("7.00"), refunds=0, fees=0, landed_cogs=0,
        advertising_spend=0, orders=1, units=1, sessions=0,
        currency="EUR", source="shopify_graphql", costs_complete=False,
    ))
    db.commit(); db.close()

    daily = client.get("/api/dashboard/commerce?days=30").json()["daily"]
    dates = [dt.date.fromisoformat(row["date"]) for row in daily]
    assert dates == [dates[0] + dt.timedelta(days=i) for i in range(len(dates))], \
        "the series must have one entry per calendar day, or the slope is wrong"
    assert dates[-1] == dt.datetime.now(dt.timezone.utc).date(), \
        "quiet days run up to today; a trailing silence must not be hidden"

    quiet = [row for row in daily
             if row["date"] == (old + dt.timedelta(days=1)).isoformat()]
    assert len(quiet) == 1
    assert quiet[0]["revenue"] == 0 and quiet[0]["orders"] == 0


def test_commerce_dashboard_withholds_the_margin_when_costs_are_incomplete():
    """An unknown margin leaves the profit line absent. Drawing one from costs
    nobody entered would put invented money on the screen."""
    result = client.get("/api/dashboard/commerce?days=30").json()
    assert result["profit"] is None
    assert result["margin"] is None


def test_production_refuses_to_start_without_auth(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://example")

    with pytest.raises(RuntimeError, match="authentication and tenant scoping"):
        _validate_runtime_safety()


def test_production_refuses_sqlite(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("JWT_SECRET", "a-secure-test-secret-that-is-long-enough")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./aco_dev.db")

    with pytest.raises(RuntimeError, match="SQLite"):
        _validate_runtime_safety()


def test_production_requires_credential_encryption_key(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("JWT_SECRET", "a-secure-test-secret-that-is-long-enough")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://example")
    monkeypatch.delenv("CREDENTIAL_ENCRYPTION_KEYS", raising=False)
    monkeypatch.delenv("CREDENTIAL_ACTIVE_KEY_VERSION", raising=False)

    with pytest.raises(RuntimeError, match="required in production"):
        _validate_runtime_safety()


def test_production_requires_durable_queue(monkeypatch):
    import base64
    import json

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("JWT_SECRET", "a-secure-test-secret-that-is-long-enough")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://example")
    monkeypatch.setenv(
        "CREDENTIAL_ENCRYPTION_KEYS",
        json.dumps({"v1": base64.b64encode(b"a" * 32).decode()}),
    )
    monkeypatch.setenv("CREDENTIAL_ACTIVE_KEY_VERSION", "v1")
    monkeypatch.setenv("QUEUE_ENABLED", "false")

    with pytest.raises(RuntimeError, match="QUEUE_ENABLED=true"):
        _validate_runtime_safety()


def test_health():
    assert client.get("/health").json() == {"status": "ok"}


def test_metrics_and_security_headers_are_exposed_without_high_cardinality_ids():
    response = client.get("/health")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-request-id"]
    metrics_response = client.get("/metrics")
    assert metrics_response.status_code == 200
    assert "aco_http_requests_total" in metrics_response.text
    assert 'path="/health"' in metrics_response.text


def test_evaluate_ranks_and_persists():
    body = {
        "explain": True,  # provider is 'none' -> deterministic template
        "products": [
            {"name": "Molds", "price": 27, "cogs": 6.5, "fba_fee": 3.3, "monthly_sales": 600,
             "ppc_per_unit": 2.5, "dominant_brands": 2, "median_reviews": 180},
            {"name": "Hose", "price": 38, "cogs": 15, "fba_fee": 8.74, "monthly_sales": 350,
             "ppc_per_unit": 4, "size_score": 0, "dominant_brands": 5, "median_reviews": 900, "demand_score": 0.5},
        ],
    }
    r = client.post("/api/product-hunter/evaluate", json=body)
    assert r.status_code == 200
    data = r.json()
    assert data["results"][0]["name"] == "Molds"        # BUY ranks first
    assert data["results"][0]["verdict"] == "BUY"
    assert data["results"][-1]["verdict"] == "AVOID"     # hose gated out
    assert data["results"][0]["whatif"]["break_even_price"] > 0
    assert data["weights"]["margin"] == 25
    assert data["results"][0]["explanation"]             # template explanation present

    hist = client.get("/api/product-hunter/history").json()
    assert any(h["name"] == "Molds" for h in hist["results"])


def test_evaluate_validation_422():
    bad = {"products": [{"name": "x", "price": -5, "cogs": 4, "fba_fee": 3, "monthly_sales": 100}]}
    assert client.post("/api/product-hunter/evaluate", json=bad).status_code == 422


def test_ppc_analyze_and_history():
    body = {
        "name": "Camp A", "break_even_acos": 0.35, "explain": False,
        "keywords": [
            {"keyword": "good kw", "clicks": 50, "spend": 40, "sales": 200, "orders": 8},
            {"keyword": "waste kw", "clicks": 30, "spend": 25, "sales": 0, "orders": 0},
        ],
    }
    r = client.post("/api/ppc/analyze", json=body)
    assert r.status_code == 200
    data = r.json()
    assert data["summary"]["wasted_spend"] == 25.0
    actions = {f["keyword"]: f["action"] for f in data["findings"]}
    assert actions["waste kw"] == "NEGATE"

    hist = client.get("/api/ppc/history").json()
    assert any(h["campaign"] == "Camp A" for h in hist["results"])


def test_inventory_analyze():
    body = {
        "explain": False,
        "skus": [
            {"name": "Low", "on_hand": 40, "avg_daily_sales": 5, "lead_time_days": 30, "unit_cost": 6.5},
            {"name": "Over", "on_hand": 1000, "avg_daily_sales": 5, "lead_time_days": 30, "unit_cost": 9},
        ],
    }
    r = client.post("/api/inventory/analyze", json=body)
    assert r.status_code == 200
    statuses = {f["name"]: f["status"] for f in r.json()["findings"]}
    assert statuses["Low"] == "REORDER_NOW"
    assert statuses["Over"] == "OVERSTOCK"


def test_autopilot_money_filter():
    # an impossible profit bar -> nothing queued
    r = client.post("/api/autopilot/scan", json={"niches": ["kitchen"], "min_monthly_profit": 10_000_000})
    assert r.status_code == 200
    assert r.json() == {"queued": 0, "skipped": 1}


def test_autopilot_scan_enqueues_once_with_same_idempotency_key(monkeypatch):
    from app import queueing

    monkeypatch.setenv("QUEUE_ENABLED", "true")
    monkeypatch.setenv("REDIS_URL", "redis://example.invalid/0")
    calls = []

    def fake_enqueue(**kwargs):
        calls.append(kwargs)
        return kwargs["job_id"]

    monkeypatch.setattr(queueing, "enqueue_autopilot_scan", fake_enqueue)
    headers = {"Idempotency-Key": "queue-test-key"}
    first = client.post("/api/autopilot/scan", headers=headers, json={"niches": ["kitchen"]})
    second = client.post("/api/autopilot/scan", headers=headers, json={"niches": ["kitchen"]})

    assert first.status_code == 200
    assert first.json()["status"] == "queued"
    assert second.json() == first.json()
    assert len(calls) == 1


def test_autopilot_scan_queue_and_decision():
    # relaxed criteria so both niches queue deterministically
    r = client.post("/api/autopilot/scan", json={
        "niches": ["kitchen", "pet"], "limit": 8,
        "only_buy": False, "min_margin": 0, "min_monthly_profit": 0,
    })
    assert r.status_code == 200
    assert r.json()["queued"] == 2

    # queue lists pending plans, money-sorted, with a listing + agent steps + total
    resp = client.get("/api/autopilot/queue").json()
    q = resp["results"]
    assert len(q) >= 2
    assert resp["total_monthly_profit"] > 0
    assert q[0]["monthly_profit"] >= q[1]["monthly_profit"]   # sorted by money
    item = q[0]
    assert item["status"] == "pending"
    assert item["product_name"] and item["verdict"] in ("BUY", "CAUTION", "AVOID")
    assert item["listing_draft"] and len(item["steps"]) >= 3

    # approve one -> status flips, drops out of the pending list
    dec = client.post(f"/api/autopilot/{item['id']}/decision", json={"action": "approve"})
    assert dec.status_code == 200 and dec.json()["status"] == "approved"
    pending_ids = {i["id"] for i in client.get("/api/autopilot/queue").json()["results"]}
    assert item["id"] not in pending_ids
    approved = client.get("/api/autopilot/queue?status=approved").json()["results"]
    assert any(i["id"] == item["id"] for i in approved)

    # bad action -> 422/400; unknown id -> 404
    assert client.post(f"/api/autopilot/{item['id']}/decision", json={"action": "nope"}).status_code == 400
    assert client.post("/api/autopilot/not-a-uuid/decision", json={"action": "approve"}).status_code == 404

    # the approved plan shows up in the portfolio with aggregated capital
    pf = client.get("/api/autopilot/portfolio").json()
    assert pf["count"] >= 1
    assert any(i["id"] == item["id"] for i in pf["items"])
    assert pf["total_monthly_profit"] > 0


def test_daily_report_aggregates():
    # The posts above persisted data into the shared DB; the report should aggregate it.
    r = client.get("/api/report/daily?explain=false")
    assert r.status_code == 200
    data = r.json()
    assert data["money_at_stake_usd"] >= 25.0
    assert len(data["items"]) > 0
    assert data["items"][0]["severity"] in ("critical", "warning", "info")


def _queue_one_plan():
    """Put a single pending plan in the queue and return its id."""
    db = _TestSession()
    store = crud.get_or_create_dev_store(db)
    row = crud.save_recommendation(
        db, store_id=store.id, module="autopilot", severity="info",
        title="pet bowls", detail={"status": "pending", "product": {"name": "bowl"}})
    plan_id = str(row.id)
    db.close()
    return plan_id


def _decide(plan_id, action):
    """One click. The browser mints a fresh idempotency key every time, so this
    is exactly what a second click looks like to the server."""
    import uuid

    return client.post(f"/api/autopilot/{plan_id}/decision", json={"action": action},
                       headers={"Idempotency-Key": str(uuid.uuid4())})


def test_clicking_approve_twice_does_not_decide_twice():
    plan_id = _queue_one_plan()
    assert _decide(plan_id, "approve").json() == {"status": "approved"}
    second = _decide(plan_id, "approve")
    assert second.status_code == 200
    assert second.json() == {"status": "approved"}

    from sqlalchemy import select

    db = _TestSession()
    events = [e for e in db.scalars(select(models.AuditEvent))
              if e.resource_id == plan_id]
    assert len(events) == 1, "the second click wrote a second decision"
    db.close()


def test_a_stale_tab_cannot_overturn_a_decision_made_elsewhere():
    plan_id = _queue_one_plan()
    assert _decide(plan_id, "approve").json() == {"status": "approved"}

    contradiction = _decide(plan_id, "dismiss")
    assert contradiction.status_code == 409
    assert "already approved" in contradiction.json()["detail"]

    db = _TestSession()
    row = crud.get_recommendation(db, plan_id)
    assert (row.detail or {}).get("status") == "approved"
    db.close()


def test_dismissing_twice_is_equally_settled():
    plan_id = _queue_one_plan()
    assert _decide(plan_id, "dismiss").json() == {"status": "dismissed"}
    assert _decide(plan_id, "dismiss").json() == {"status": "dismissed"}
    assert _decide(plan_id, "approve").status_code == 409
