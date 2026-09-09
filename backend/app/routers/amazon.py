"""Amazon SP-API: OAuth, sync, sales reports and the cost ledger."""

from .. import amazon_sp_api
from .. import credentials
from ..db import crud, models
from ..db.session import declare_tenant, get_session
from ..queueing import queue_enabled
from ..schemas import (
    AmazonAuthorizationResponse, AmazonConnectionResponse, AmazonSyncStatusResponse,
    AmazonSalesDailyOut, AmazonSalesSummaryResponse, AmazonSkuCostOut,
    AmazonSkuCostsRequest, AmazonSkuCostsResponse, BackgroundJobResponse)
from fastapi import APIRouter, Depends, HTTPException, Header, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session
import datetime as dt

from ..runtime import log
from ..deps import _actor_id, _idempotency_key

router = APIRouter()


@router.post("/api/integrations/amazon/authorize", response_model=AmazonAuthorizationResponse)
def amazon_authorize(db: Session = Depends(get_session)) -> AmazonAuthorizationResponse:
    store = crud.get_or_create_dev_store(db)
    url = amazon_sp_api.create_authorization(
        db, store_id=store.id, actor_id=_actor_id(store.user_id)
    )
    return AmazonAuthorizationResponse(authorization_url=url)


@router.get("/api/integrations/amazon/callback", response_model=AmazonConnectionResponse)
def amazon_oauth_callback(state: str, selling_partner_id: str, spapi_oauth_code: str,
                          response: Response,
                          db: Session = Depends(get_session)) -> AmazonConnectionResponse:
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    oauth_state = amazon_sp_api.consume_authorization_state(db, state)
    # The state row is how this callback learns whose store it is.
    declare_tenant(db, oauth_state.store_id)
    try:
        tokens = amazon_sp_api.exchange_authorization_code(spapi_oauth_code)
        credentials.store_credential(
            db, store_id=oauth_state.store_id, provider="amazon-sp-api",
            secret=tokens["refresh_token"],
        )
        store = db.get(models.Store, oauth_state.store_id)
        if store is None:
            raise RuntimeError("OAuth tenant store no longer exists.")
        store.seller_id = selling_partner_id
        db.add(store)
        db.commit()
        crud.append_audit_event(
            db, store_id=store.id, actor_id=oauth_state.actor_id,
            action="amazon.connected", resource_type="integration",
            resource_id="amazon-sp-api", after={"seller_id": selling_partner_id},
        )
        return AmazonConnectionResponse(connected=True, seller_id=selling_partner_id)
    except Exception as exc:
        log.exception("Amazon OAuth callback failed")
        raise HTTPException(status_code=502, detail="Amazon authorization failed") from exc


@router.get("/api/integrations/amazon/account", response_model=AmazonConnectionResponse)
def amazon_account(region: str = "EU", db: Session = Depends(get_session)) -> AmazonConnectionResponse:
    store = crud.get_or_create_dev_store(db)
    try:
        payload = amazon_sp_api.client_for_store(db, store_id=store.id, region=region)
        data = payload.marketplace_participations().get("payload", {})
        return AmazonConnectionResponse(
            connected=True, seller_id=store.seller_id,
            marketplaces=data.get("marketplaceParticipations", []),
        )
    except amazon_sp_api.AmazonAuthorizationError:
        return AmazonConnectionResponse(connected=False, seller_id=store.seller_id)


@router.post("/api/integrations/amazon/sync", response_model=BackgroundJobResponse)
def amazon_sync(region: str = "EU", db: Session = Depends(get_session),
                idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")) -> BackgroundJobResponse:
    if not queue_enabled():
        raise HTTPException(status_code=503, detail="Background queue is required for Amazon sync")
    store = crud.get_or_create_dev_store(db)
    if not store.seller_id:
        raise HTTPException(status_code=409, detail="Amazon account is not connected")
    key = _idempotency_key(idempotency_key)
    record, is_new = crud.claim_idempotency(
        db, store_id=store.id, operation="amazon.sync", key=key
    )
    if not is_new:
        status = "finished" if record.status == "completed" else record.status
        return BackgroundJobResponse(job_id=str(record.id), status=status, result=record.response)
    try:
        from ..queueing import enqueue_amazon_sync
        job_id = enqueue_amazon_sync(
            job_id=str(record.id), tenant_id=str(store.id),
            actor_id=str(_actor_id(store.user_id)), region=region,
        )
        return BackgroundJobResponse(job_id=job_id, status="queued")
    except Exception as exc:
        crud.fail_idempotency(db, record)
        log.exception("Failed to enqueue Amazon sync")
        raise HTTPException(status_code=503, detail="Background queue unavailable") from exc


@router.get("/api/integrations/amazon/sync", response_model=AmazonSyncStatusResponse)
def amazon_sync_status(db: Session = Depends(get_session)) -> AmazonSyncStatusResponse:
    store = crud.get_or_create_dev_store(db)
    row = db.scalar(select(models.AmazonSyncRun).where(
        models.AmazonSyncRun.store_id == store.id
    ).order_by(models.AmazonSyncRun.started_at.desc()).limit(1))
    if row is None:
        return AmazonSyncStatusResponse(status="never")
    return AmazonSyncStatusResponse(
        status=row.status, sync_id=str(row.id), listings=row.listings_count,
        inventory=row.inventory_count, started_at=row.started_at, completed_at=row.completed_at,
    )


@router.post("/api/integrations/amazon/reports/sales", response_model=BackgroundJobResponse)
def amazon_sales_report(marketplace_id: str, region: str = "EU",
                        days: int = Query(default=30, ge=7, le=30),
                        db: Session = Depends(get_session),
                        idempotency_key: str | None = Header(
                            default=None, alias="Idempotency-Key")) -> BackgroundJobResponse:
    if not queue_enabled():
        raise HTTPException(status_code=503, detail="Background queue is required for reports")
    store = crud.get_or_create_dev_store(db)
    if not store.seller_id:
        raise HTTPException(status_code=409, detail="Amazon account is not connected")
    key = _idempotency_key(idempotency_key)
    record, is_new = crud.claim_idempotency(
        db, store_id=store.id,
        operation=f"amazon.sales_report:{marketplace_id}:{days}", key=key,
    )
    if not is_new:
        status = "finished" if record.status == "completed" else record.status
        return BackgroundJobResponse(job_id=str(record.id), status=status,
                                     result=record.response)
    try:
        from ..queueing import enqueue_amazon_sales_report
        job_id = enqueue_amazon_sales_report(
            job_id=str(record.id), tenant_id=str(store.id),
            actor_id=str(_actor_id(store.user_id)), region=region,
            marketplace_id=marketplace_id, days=days,
        )
        return BackgroundJobResponse(job_id=job_id, status="queued")
    except Exception as exc:
        crud.fail_idempotency(db, record)
        log.exception("Failed to enqueue Amazon sales report")
        raise HTTPException(status_code=503, detail="Background queue unavailable") from exc


@router.post("/api/integrations/amazon/finances/sync", response_model=BackgroundJobResponse)
def amazon_finances_sync(marketplace_id: str, region: str = "EU",
                         days: int = Query(default=30, ge=7, le=90),
                         db: Session = Depends(get_session),
                         idempotency_key: str | None = Header(
                             default=None, alias="Idempotency-Key")) -> BackgroundJobResponse:
    if not queue_enabled():
        raise HTTPException(status_code=503, detail="Background queue is required for finances")
    store = crud.get_or_create_dev_store(db)
    if not store.seller_id:
        raise HTTPException(status_code=409, detail="Amazon account is not connected")
    key = _idempotency_key(idempotency_key)
    record, is_new = crud.claim_idempotency(
        db, store_id=store.id,
        operation=f"amazon.finances_sync:{marketplace_id}:{days}", key=key,
    )
    if not is_new:
        status = "finished" if record.status == "completed" else record.status
        return BackgroundJobResponse(job_id=str(record.id), status=status,
                                     result=record.response)
    try:
        from ..queueing import enqueue_amazon_finances_sync
        job_id = enqueue_amazon_finances_sync(
            job_id=str(record.id), tenant_id=str(store.id),
            actor_id=str(_actor_id(store.user_id)), region=region,
            marketplace_id=marketplace_id, days=days,
        )
        return BackgroundJobResponse(job_id=job_id, status="queued")
    except Exception as exc:
        crud.fail_idempotency(db, record)
        log.exception("Failed to enqueue Amazon finances sync")
        raise HTTPException(status_code=503, detail="Background queue unavailable") from exc


@router.get("/api/integrations/amazon/reports/sales",
         response_model=AmazonSalesSummaryResponse)
def amazon_sales_summary(marketplace_id: str,
                         days: int = Query(default=30, ge=7, le=90),
                         db: Session = Depends(get_session)) -> AmazonSalesSummaryResponse:
    store = crud.get_or_create_dev_store(db)
    cutoff = dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=days)
    rows = list(db.scalars(select(models.AmazonSalesDaily).where(
        models.AmazonSalesDaily.store_id == store.id,
        models.AmazonSalesDaily.marketplace_id == marketplace_id,
        models.AmazonSalesDaily.sales_date >= cutoff,
    ).order_by(models.AmazonSalesDaily.sales_date.desc())))
    return AmazonSalesSummaryResponse(
        marketplace_id=marketplace_id, days=days,
        total_sales=round(sum(float(row.ordered_sales) for row in rows), 2),
        total_units=sum(row.units_ordered for row in rows),
        total_order_items=sum(row.order_items for row in rows),
        currency=next((row.currency for row in rows if row.currency), None),
        last_synced_at=max((row.synced_at for row in rows), default=None),
        results=[AmazonSalesDailyOut(
            date=row.sales_date.isoformat(), ordered_sales=float(row.ordered_sales),
            currency=row.currency, units_ordered=row.units_ordered,
            order_items=row.order_items, page_views=row.page_views,
            sessions=row.sessions, buy_box_percentage=row.buy_box_percentage,
        ) for row in rows],
    )


@router.put("/api/integrations/amazon/costs", response_model=AmazonSkuCostsResponse)
def replace_amazon_sku_costs(req: AmazonSkuCostsRequest,
                             db: Session = Depends(get_session),
                             idempotency_key: str | None = Header(
                                 default=None, alias="Idempotency-Key")) -> AmazonSkuCostsResponse:
    store = crud.get_or_create_dev_store(db)
    key = _idempotency_key(idempotency_key)
    record, is_new = crud.claim_idempotency(
        db, store_id=store.id, operation=f"amazon.sku_costs:{req.marketplace_id}", key=key
    )
    if not is_new:
        if record.status == "completed":
            rows = list(db.scalars(select(models.AmazonSkuCost).where(
                models.AmazonSkuCost.store_id == store.id,
                models.AmazonSkuCost.marketplace_id == req.marketplace_id,
            ).order_by(models.AmazonSkuCost.sku)))
            return AmazonSkuCostsResponse(
                marketplace_id=req.marketplace_id,
                results=[AmazonSkuCostOut(
                    sku=row.sku, landed_cost=float(row.landed_cost),
                    currency=row.currency, updated_at=row.updated_at,
                ) for row in rows],
            )
        raise HTTPException(status_code=409, detail="Cost import is already in progress or failed")
    try:
        now = dt.datetime.now(dt.timezone.utc)
        seen: set[str] = set()
        for cost in req.costs:
            sku = cost.sku.strip()
            if not sku or sku in seen:
                raise HTTPException(status_code=422, detail="SKU values must be non-empty and unique")
            seen.add(sku)
            row = db.scalar(select(models.AmazonSkuCost).where(
                models.AmazonSkuCost.store_id == store.id,
                models.AmazonSkuCost.marketplace_id == req.marketplace_id,
                models.AmazonSkuCost.sku == sku,
            )) or models.AmazonSkuCost(
                store_id=store.id, marketplace_id=req.marketplace_id, sku=sku
            )
            row.landed_cost = cost.landed_cost
            row.currency = cost.currency.upper()
            row.updated_at = now
            db.add(row)
        result = {"marketplace_id": req.marketplace_id, "updated": len(seen)}
        crud.append_audit_event(
            db, store_id=store.id, actor_id=_actor_id(store.user_id),
            action="amazon.sku_costs.updated", resource_type="amazon_sku_cost",
            idempotency_key=key, after=result, commit=False,
        )
        crud.complete_idempotency(db, record, result)
    except Exception:
        db.rollback()
        crud.fail_idempotency(db, record)
        raise
    rows = list(db.scalars(select(models.AmazonSkuCost).where(
        models.AmazonSkuCost.store_id == store.id,
        models.AmazonSkuCost.marketplace_id == req.marketplace_id,
    ).order_by(models.AmazonSkuCost.sku)))
    return AmazonSkuCostsResponse(
        marketplace_id=req.marketplace_id,
        results=[AmazonSkuCostOut(
            sku=row.sku, landed_cost=float(row.landed_cost),
            currency=row.currency, updated_at=row.updated_at,
        ) for row in rows],
    )


@router.get("/api/integrations/amazon/costs", response_model=AmazonSkuCostsResponse)
def amazon_sku_costs(marketplace_id: str,
                     db: Session = Depends(get_session)) -> AmazonSkuCostsResponse:
    store = crud.get_or_create_dev_store(db)
    rows = list(db.scalars(select(models.AmazonSkuCost).where(
        models.AmazonSkuCost.store_id == store.id,
        models.AmazonSkuCost.marketplace_id == marketplace_id,
    ).order_by(models.AmazonSkuCost.sku)))
    return AmazonSkuCostsResponse(
        marketplace_id=marketplace_id,
        results=[AmazonSkuCostOut(
            sku=row.sku, landed_cost=float(row.landed_cost),
            currency=row.currency, updated_at=row.updated_at,
        ) for row in rows],
    )
