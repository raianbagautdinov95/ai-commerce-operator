"""ROI and commerce dashboards, and the labelled demo seed."""

from .. import commerce_engine
from ..db import crud, models
from ..db.session import get_session
from ..schemas import (
    DemoSeedResponse, ChannelPerformanceOut, CommerceDailyOut, CommerceDashboardResponse,
    RoiDailyOut,
    RoiDashboardResponse)
from fastapi import APIRouter, Depends, HTTPException, Header, Query
from sqlalchemy import delete, select
from sqlalchemy.orm import Session
import datetime as dt

from ..deps import _actor_id, _idempotency_key

router = APIRouter()


@router.get("/api/dashboard/roi", response_model=RoiDashboardResponse)
def roi_dashboard(marketplace_id: str | None = None,
                  days: int = Query(default=30, ge=7, le=90),
                  db: Session = Depends(get_session)) -> RoiDashboardResponse:
    """Actual sales KPIs only; profit stays unavailable until matched costs exist."""
    store = crud.get_or_create_dev_store(db)
    if marketplace_id is None:
        marketplace_id = db.scalar(select(models.AmazonSalesDaily.marketplace_id).where(
            models.AmazonSalesDaily.store_id == store.id
        ).order_by(models.AmazonSalesDaily.synced_at.desc()).limit(1))
    if marketplace_id is None:
        return RoiDashboardResponse(
            period_days=days, revenue=0, previous_revenue=0, units=0,
            order_items=0, sessions=0, freshness="no_data",
            missing_profit_inputs=["landed_cogs", "amazon_fees", "advertising_spend"],
        )

    today = dt.datetime.now(dt.timezone.utc).date()
    current_start = today - dt.timedelta(days=days)
    previous_start = current_start - dt.timedelta(days=days)
    all_rows = list(db.scalars(select(models.AmazonSalesDaily).where(
        models.AmazonSalesDaily.store_id == store.id,
        models.AmazonSalesDaily.marketplace_id == marketplace_id,
        models.AmazonSalesDaily.sales_date >= previous_start,
        models.AmazonSalesDaily.sales_date < today,
    ).order_by(models.AmazonSalesDaily.sales_date)))
    current = [row for row in all_rows if row.sales_date >= current_start]
    previous = [row for row in all_rows if row.sales_date < current_start]
    revenue = round(sum(float(row.ordered_sales) for row in current), 2)
    previous_revenue = round(sum(float(row.ordered_sales) for row in previous), 2)
    costs = list(db.scalars(select(models.AmazonCostDaily).where(
        models.AmazonCostDaily.store_id == store.id,
        models.AmazonCostDaily.marketplace_id == marketplace_id,
        models.AmazonCostDaily.cost_date >= current_start,
        models.AmazonCostDaily.cost_date < today,
    )))
    fees_rows = [row for row in costs if row.category == "amazon_fees"]
    amazon_fees = round(sum(float(row.amount) for row in fees_rows), 2) if fees_rows else None
    ads_rows = [row for row in costs if row.category == "advertising_spend"]
    advertising_spend = round(sum(float(row.amount) for row in ads_rows), 2) if ads_rows else None
    report_run = db.scalar(select(models.AmazonReportRun).where(
        models.AmazonReportRun.store_id == store.id,
        models.AmazonReportRun.marketplace_id == marketplace_id,
        models.AmazonReportRun.status == "completed",
        models.AmazonReportRun.date_start <= current_start,
        models.AmazonReportRun.date_end >= today - dt.timedelta(days=1),
    ).order_by(models.AmazonReportRun.completed_at.desc()).limit(1))
    sku_sales = list(db.scalars(select(models.AmazonSalesSkuPeriod).where(
        models.AmazonSalesSkuPeriod.report_run_id == report_run.id
    ))) if report_run else []
    sku_cost_rows = list(db.scalars(select(models.AmazonSkuCost).where(
        models.AmazonSkuCost.store_id == store.id,
        models.AmazonSkuCost.marketplace_id == marketplace_id,
    )))
    cost_by_sku = {row.sku: row for row in sku_cost_rows}
    sold_skus = [row for row in sku_sales if row.units_ordered > 0]
    missing_cost_skus = sorted(row.sku for row in sold_skus if row.sku not in cost_by_sku)
    covered_units = sum(row.units_ordered for row in sold_skus if row.sku in cost_by_sku)
    total_sku_units = sum(row.units_ordered for row in sold_skus)
    cogs_coverage = round(covered_units / total_sku_units * 100, 1) if total_sku_units else None
    landed_cogs = None
    if sold_skus and not missing_cost_skus:
        landed_cogs = round(sum(
            row.units_ordered * float(cost_by_sku[row.sku].landed_cost) for row in sold_skus
        ), 2)
    profit = margin = roi = None
    if landed_cogs is not None and amazon_fees is not None and advertising_spend is not None:
        total_cost = landed_cogs + amazon_fees + advertising_spend
        profit = round(revenue - total_cost, 2)
        margin = round(profit / revenue, 4) if revenue else None
        roi = round(profit / total_cost, 4) if total_cost else None
    order_items = sum(row.order_items for row in current)
    sessions = sum(row.sessions for row in current)
    last_synced = max((row.synced_at for row in current), default=None)
    now = dt.datetime.now(dt.timezone.utc)
    if last_synced is None:
        freshness = "no_data"
    else:
        comparable = last_synced if last_synced.tzinfo else last_synced.replace(tzinfo=dt.timezone.utc)
        age = now - comparable
        freshness = "fresh" if age <= dt.timedelta(hours=36) else (
            "stale" if age <= dt.timedelta(days=7) else "outdated"
        )
    revenue_change = None
    if previous_revenue > 0:
        revenue_change = round((revenue - previous_revenue) / previous_revenue * 100, 1)
    return RoiDashboardResponse(
        marketplace_id=marketplace_id, demo_data=marketplace_id == "DEMO-A1",
        period_days=days, revenue=revenue,
        previous_revenue=previous_revenue,
        revenue_change_percentage=revenue_change,
        units=sum(row.units_ordered for row in current), order_items=order_items,
        sessions=sessions,
        conversion_rate=round(order_items / sessions * 100, 2) if sessions else None,
        average_order_value=round(revenue / order_items, 2) if order_items else None,
        currency=next((row.currency for row in current if row.currency), None),
        last_synced_at=last_synced, freshness=freshness,
        amazon_fees=amazon_fees, advertising_spend=advertising_spend,
        landed_cogs=landed_cogs, profit=profit, margin=margin, roi=roi,
        cogs_coverage_percentage=cogs_coverage, missing_cost_skus=missing_cost_skus[:100],
        missing_profit_inputs=(["landed_cogs"] if landed_cogs is None else []) + (
            ["advertising_spend"] if advertising_spend is None else []) + (
            [] if amazon_fees is not None else ["amazon_fees"]
        ),
        daily=[RoiDailyOut(
            date=row.sales_date.isoformat(), revenue=float(row.ordered_sales),
            units=row.units_ordered, order_items=row.order_items, sessions=row.sessions,
        ) for row in current],
    )


@router.get("/api/dashboard/commerce", response_model=CommerceDashboardResponse)
def commerce_dashboard(days: int = Query(default=30, ge=7, le=90),
                       db: Session = Depends(get_session)) -> CommerceDashboardResponse:
    store = crud.get_or_create_dev_store(db)
    cutoff = dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=days)
    all_channels = list(db.scalars(select(models.ChannelConnection).where(
        models.ChannelConnection.store_id == store.id,
        models.ChannelConnection.status == "connected",
    ).order_by(models.ChannelConnection.display_name)))
    demo_channels = [channel for channel in all_channels if
                     bool((channel.settings or {}).get("demo")) or
                     channel.external_account_id.startswith("DEMO-")]
    real_channels = [channel for channel in all_channels if channel not in demo_channels]
    channels = real_channels or demo_channels
    channel_ids = [channel.id for channel in channels]
    metrics = list(db.scalars(select(models.CommerceDailyMetric).where(
        models.CommerceDailyMetric.store_id == store.id,
        models.CommerceDailyMetric.metric_date >= cutoff,
        models.CommerceDailyMetric.channel_id.in_(channel_ids),
    ))) if channel_ids else []
    total_revenue = round(sum(float(row.revenue) for row in metrics), 2)
    results = []
    for channel in channels:
        rows = [row for row in metrics if row.channel_id == channel.id]
        revenue = round(sum(float(row.revenue) for row in rows), 2)
        cost = sum(float(row.refunds) + float(row.fees) + float(row.landed_cogs) +
                   float(row.advertising_spend) for row in rows)
        complete = bool(rows) and all(row.costs_complete for row in rows)
        results.append(ChannelPerformanceOut(
            channel_id=str(channel.id), provider=channel.provider,
            display_name=channel.display_name, status=channel.status,
            revenue=revenue, profit=round(revenue - cost, 2) if complete else None,
            orders=sum(row.orders for row in rows), currency=channel.currency,
            share_percentage=round(revenue / total_revenue * 100, 1) if total_revenue else 0,
        ))
    total_cost = sum(float(row.refunds) + float(row.fees) + float(row.landed_cogs) +
                     float(row.advertising_spend) for row in metrics)
    all_costs_complete = bool(metrics) and all(row.costs_complete for row in metrics)
    profit = round(total_revenue - total_cost, 2) if all_costs_complete else None

    # One entry per calendar day, quiet days included. The chart and the commerce
    # engine read the same series from the same builder on purpose: the engine's
    # stall rule counts silent days, so a series that dropped them would make a
    # stalled store look like a busy one.
    series = commerce_engine.daily_series(
        [{"date": row.metric_date, "revenue": float(row.revenue),
          "refunds": float(row.refunds),
          "advertising_spend": float(row.advertising_spend),
          "orders": row.orders, "units": row.units,
          "costs_complete": row.costs_complete} for row in metrics],
        today=dt.datetime.now(dt.timezone.utc).date())
    daily = [CommerceDailyOut(date=day["date"].isoformat(), revenue=day["revenue"],
                              orders=day["orders"], units=day["units"])
             for day in series]

    # Freshness is judged on the same thresholds as the Amazon dashboard, so one
    # badge means one thing wherever it appears.
    # A row may carry no sync stamp at all; comparing one against a datetime
    # would raise rather than report freshness.
    last_synced = max((row.synced_at for row in metrics if row.synced_at is not None),
                      default=None)
    if last_synced is None:
        freshness = "no_data"
    else:
        comparable = last_synced if last_synced.tzinfo else last_synced.replace(
            tzinfo=dt.timezone.utc)
        age = dt.datetime.now(dt.timezone.utc) - comparable
        freshness = "fresh" if age <= dt.timedelta(hours=36) else (
            "stale" if age <= dt.timedelta(days=7) else "outdated")

    return CommerceDashboardResponse(
        period_days=days,
        demo_data=bool(channels) and not real_channels,
        revenue=total_revenue,
        profit=profit,
        orders=sum(row.orders for row in metrics), units=sum(row.units for row in metrics),
        currency=channels[0].currency if channels else None, channels=results,
        daily=daily,
        margin=round(profit / total_revenue, 4) if profit is not None and total_revenue else None,
        last_synced_at=last_synced, freshness=freshness,
    )


@router.post("/api/demo/seed", response_model=DemoSeedResponse)
def seed_demo_store(db: Session = Depends(get_session),
                    idempotency_key: str | None = Header(
                        default=None, alias="Idempotency-Key")) -> DemoSeedResponse:
    """Replace only DEMO-A1 rows with a deterministic synthetic showcase dataset."""
    store = crud.get_or_create_dev_store(db)
    key = _idempotency_key(idempotency_key)
    record, is_new = crud.claim_idempotency(
        db, store_id=store.id, operation="demo.seed:v1", key=key
    )
    if not is_new and record.status == "completed" and record.response:
        return DemoSeedResponse(**record.response)
    if not is_new:
        raise HTTPException(status_code=409, detail="Demo seed is already in progress or failed")
    marketplace_id = "DEMO-A1"
    try:
        demo_channels = list(db.scalars(select(models.ChannelConnection).where(
            models.ChannelConnection.store_id == store.id,
            models.ChannelConnection.external_account_id.like("DEMO-%"),
        )))
        demo_channel_ids = [channel.id for channel in demo_channels]
        if demo_channel_ids:
            db.execute(delete(models.CommerceDailyMetric).where(
                models.CommerceDailyMetric.store_id == store.id,
                models.CommerceDailyMetric.channel_id.in_(demo_channel_ids),
            ))
            db.execute(delete(models.ChannelConnection).where(
                models.ChannelConnection.store_id == store.id,
                models.ChannelConnection.id.in_(demo_channel_ids),
            ))
        run_ids = list(db.scalars(select(models.AmazonReportRun.id).where(
            models.AmazonReportRun.store_id == store.id,
            models.AmazonReportRun.marketplace_id == marketplace_id,
        )))
        if run_ids:
            db.execute(delete(models.AmazonSalesSkuPeriod).where(
                models.AmazonSalesSkuPeriod.report_run_id.in_(run_ids)
            ))
        for model in (models.AmazonSalesDaily, models.AmazonCostDaily,
                      models.AmazonSkuCost, models.AmazonReportRun):
            db.execute(delete(model).where(
                model.store_id == store.id, model.marketplace_id == marketplace_id
            ))

        today = dt.datetime.now(dt.timezone.utc).date()
        now = dt.datetime.now(dt.timezone.utc)
        channel_specs = [
            ("amazon", "DEMO-AMAZON", "Amazon US", 0.47, 0.226),
            ("shopify", "DEMO-SHOPIFY", "Northstar Store", 0.36, 0.029),
            ("woocommerce", "DEMO-WOO", "Wholesale Portal", 0.17, 0.021),
        ]
        demo_channel_rows = []
        for provider, external_id, name, _share, _fee_rate in channel_specs:
            channel = models.ChannelConnection(
                store_id=store.id, provider=provider, external_account_id=external_id,
                display_name=name, status="connected", currency="USD",
                settings={"demo": True}, connected_at=now, synced_at=now,
            )
            db.add(channel)
            demo_channel_rows.append(channel)
        db.flush()
        sku_specs = [
            ("DEMO-MOLD-BLUE", 6.25, 0.52),
            ("DEMO-BOTTLE-1L", 8.40, 0.30),
            ("DEMO-DESK-MAT", 5.10, 0.18),
        ]
        for sku, cost, _share in sku_specs:
            db.add(models.AmazonSkuCost(
                store_id=store.id, marketplace_id=marketplace_id, sku=sku,
                landed_cost=cost, currency="USD", updated_at=now,
            ))
        period_units = {sku: 0 for sku, _, _ in sku_specs}
        period_sales = {sku: 0.0 for sku, _, _ in sku_specs}
        for offset in range(60, 0, -1):
            day = today - dt.timedelta(days=offset)
            growth = 1.0 + (60 - offset) * 0.006
            weekend = 1.18 if day.weekday() >= 5 else 1.0
            units = max(4, round((13 + ((offset * 7) % 9)) * growth * weekend))
            average_price = 27.90
            revenue = round(units * average_price, 2)
            orders = max(1, round(units * 0.84))
            sessions = round(orders / 0.118)
            db.add(models.AmazonSalesDaily(
                store_id=store.id, marketplace_id=marketplace_id, sales_date=day,
                ordered_sales=revenue, currency="USD", units_ordered=units,
                order_items=orders, page_views=round(sessions * 1.34), sessions=sessions,
                buy_box_percentage=96.4, synced_at=now,
            ))
            db.add(models.AmazonCostDaily(
                store_id=store.id, marketplace_id=marketplace_id, cost_date=day,
                category="amazon_fees", amount=round(revenue * 0.226, 2),
                currency="USD", source="synthetic_demo", source_count=orders, synced_at=now,
            ))
            db.add(models.AmazonCostDaily(
                store_id=store.id, marketplace_id=marketplace_id, cost_date=day,
                category="advertising_spend", amount=round(revenue * 0.118, 2),
                currency="USD", source="synthetic_demo", source_count=1, synced_at=now,
            ))
            allocated_revenue = 0.0
            allocated_orders = 0
            allocated_units = 0
            for index, ((provider, _external, _name, share, fee_rate), channel) in enumerate(
                    zip(channel_specs, demo_channel_rows)):
                channel_revenue = round(revenue - allocated_revenue, 2) if index == len(channel_specs) - 1 else round(revenue * share, 2)
                channel_orders = orders - allocated_orders if index == len(channel_specs) - 1 else round(orders * share)
                channel_units = units - allocated_units if index == len(channel_specs) - 1 else round(units * share)
                allocated_revenue += channel_revenue
                allocated_orders += channel_orders
                allocated_units += channel_units
                db.add(models.CommerceDailyMetric(
                    store_id=store.id, channel_id=channel.id, metric_date=day,
                    revenue=channel_revenue, refunds=round(channel_revenue * 0.021, 2),
                    fees=round(channel_revenue * fee_rate, 2),
                    landed_cogs=round(channel_revenue * 0.247, 2),
                    advertising_spend=round(channel_revenue * (0.118 if provider == "amazon" else 0.092), 2),
                    orders=channel_orders, units=channel_units,
                    sessions=max(channel_orders, round(channel_orders / 0.118)),
                    currency="USD", source="synthetic_demo", synced_at=now,
                    costs_complete=True,
                ))
            if offset <= 30:
                allocated = 0
                for index, (sku, _cost, share) in enumerate(sku_specs):
                    value = units - allocated if index == len(sku_specs) - 1 else round(units * share)
                    allocated += value
                    period_units[sku] += value
                    period_sales[sku] += value * average_price

        run = models.AmazonReportRun(
            store_id=store.id, marketplace_id=marketplace_id,
            report_type="GET_SALES_AND_TRAFFIC_REPORT", amazon_report_id="DEMO-REPORT",
            report_document_id="DEMO-DOCUMENT", status="completed",
            date_start=today - dt.timedelta(days=30), date_end=today - dt.timedelta(days=1),
            rows_count=30, started_at=now, completed_at=now,
        )
        db.add(run)
        db.flush()
        for sku, _cost, _share in sku_specs:
            db.add(models.AmazonSalesSkuPeriod(
                report_run_id=run.id, store_id=store.id, marketplace_id=marketplace_id,
                sku=sku, units_ordered=period_units[sku],
                ordered_sales=round(period_sales[sku], 2), currency="USD",
            ))
        result = {"marketplace_id": marketplace_id, "days": 60, "skus": 3,
                  "message": "Synthetic demo data is ready."}
        crud.append_audit_event(
            db, store_id=store.id, actor_id=_actor_id(store.user_id), action="demo.seeded",
            resource_type="demo_dataset", resource_id=marketplace_id,
            idempotency_key=key, after=result, commit=False,
        )
        crud.complete_idempotency(db, record, result)
        return DemoSeedResponse(**result)
    except Exception:
        db.rollback()
        crud.fail_idempotency(db, record)
        raise
