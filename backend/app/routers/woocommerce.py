"""WooCommerce: OAuth and sync."""

from .. import credentials
from .. import woocommerce
from ..db import crud, models
from ..db.session import declare_tenant, get_session
from ..queueing import queue_enabled
from ..schemas import (
    WooCommerceAuthorizationResponse, WooCommerceConnectionResponse, BackgroundJobResponse)
from fastapi import APIRouter, Depends, HTTPException, Header, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session
import datetime as dt
import json

from ..runtime import log
from ..deps import _actor_id, _idempotency_key

router = APIRouter()


@router.post("/api/integrations/woocommerce/authorize",
          response_model=WooCommerceAuthorizationResponse)
def woocommerce_authorize(store_url: str, db: Session = Depends(get_session)) \
        -> WooCommerceAuthorizationResponse:
    store = crud.get_or_create_dev_store(db)
    try:
        url = woocommerce.create_authorization(
            db, store_id=store.id, actor_id=_actor_id(store.user_id), store_url=store_url
        )
        return WooCommerceAuthorizationResponse(authorization_url=url)
    except (woocommerce.WooCommerceAuthorizationError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/integrations/woocommerce/account",
         response_model=WooCommerceConnectionResponse)
def woocommerce_account(db: Session = Depends(get_session)) -> WooCommerceConnectionResponse:
    store = crud.get_or_create_dev_store(db)
    channel = db.scalar(select(models.ChannelConnection).where(
        models.ChannelConnection.store_id == store.id,
        models.ChannelConnection.provider == "woocommerce",
        models.ChannelConnection.status == "connected",
        models.ChannelConnection.external_account_id.like("https://%"),
    ).order_by(models.ChannelConnection.connected_at.desc()))
    if channel is None:
        return WooCommerceConnectionResponse(connected=False)
    return WooCommerceConnectionResponse(
        connected=True, store_url=channel.external_account_id, channel_id=str(channel.id)
    )


@router.post("/api/integrations/woocommerce/sync", response_model=BackgroundJobResponse)
def woocommerce_sync(days: int = Query(default=30, ge=1, le=90),
                     db: Session = Depends(get_session),
                     idempotency_key: str | None = Header(
                         default=None, alias="Idempotency-Key")) -> BackgroundJobResponse:
    if not queue_enabled():
        raise HTTPException(status_code=503, detail="Background queue is required for WooCommerce sync")
    store = crud.get_or_create_dev_store(db)
    channel = db.scalar(select(models.ChannelConnection).where(
        models.ChannelConnection.store_id == store.id,
        models.ChannelConnection.provider == "woocommerce",
        models.ChannelConnection.status == "connected",
        models.ChannelConnection.external_account_id.like("https://%"),
    ))
    if channel is None:
        raise HTTPException(status_code=409, detail="WooCommerce store is not connected")
    record, is_new = crud.claim_idempotency(
        db, store_id=store.id, operation=f"woocommerce.sync:{channel.id}:{days}",
        key=_idempotency_key(idempotency_key),
    )
    if not is_new:
        status = "finished" if record.status == "completed" else record.status
        return BackgroundJobResponse(job_id=str(record.id), status=status, result=record.response)
    try:
        from ..queueing import enqueue_woocommerce_sync
        job_id = enqueue_woocommerce_sync(
            job_id=str(record.id), tenant_id=str(store.id),
            actor_id=str(_actor_id(store.user_id)), channel_id=str(channel.id), days=days,
        )
        return BackgroundJobResponse(job_id=job_id, status="queued")
    except Exception as exc:
        crud.fail_idempotency(db, record)
        log.exception("Failed to enqueue WooCommerce sync")
        raise HTTPException(status_code=503, detail="Background queue unavailable") from exc


@router.post("/api/integrations/woocommerce/callback",
          response_model=WooCommerceConnectionResponse)
async def woocommerce_callback(request: Request, db: Session = Depends(get_session)) \
        -> WooCommerceConnectionResponse:
    raw = await request.body()
    if len(raw) > 32 * 1024:
        raise HTTPException(status_code=413, detail="Authorization payload too large")
    try:
        payload = json.loads(raw)
        state, site, key, secret = woocommerce.consume_callback(db, payload)
        # The state row is how this callback learns whose store it is.
        declare_tenant(db, state.store_id)
        existing = list(db.scalars(select(models.ChannelConnection).where(
            models.ChannelConnection.provider == "woocommerce",
            models.ChannelConnection.external_account_id == site,
        )))
        if any(row.store_id != state.store_id for row in existing):
            raise woocommerce.WooCommerceAuthorizationError(
                "This WooCommerce store is already connected to another tenant."
            )
        channel = next(iter(existing), None) or models.ChannelConnection(
            store_id=state.store_id, provider="woocommerce", external_account_id=site,
            display_name=site.removeprefix("https://"), status="connected",
            currency="USD", settings={"permissions": "read"},
        )
        channel.status = "connected"; channel.synced_at = dt.datetime.now(dt.timezone.utc)
        db.add(channel); db.commit(); db.refresh(channel)
        credentials.store_credential(
            db, store_id=state.store_id, provider=woocommerce.credential_provider(site),
            secret=woocommerce.serialize_credentials(key, secret),
        )
        crud.append_audit_event(
            db, store_id=state.store_id, actor_id=state.actor_id,
            action="woocommerce.connected", resource_type="channel_connection",
            resource_id=str(channel.id), after={"store_url": site, "permissions": "read"},
        )
        return WooCommerceConnectionResponse(
            connected=True, store_url=site, channel_id=str(channel.id)
        )
    except (json.JSONDecodeError, woocommerce.WooCommerceAuthorizationError) as exc:
        raise HTTPException(status_code=400, detail="WooCommerce authorization failed") from exc
