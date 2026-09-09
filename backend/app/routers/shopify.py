"""Shopify: OAuth, sync, order notifications."""

from .. import credentials
from .. import entitlement
from .. import shopify
from .. import webhook_health
from ..db import crud, models
from ..db.session import declare_tenant, get_session
from ..queueing import queue_enabled
from ..schemas import (
    ShopifyAuthorizationResponse, ShopifyConnectionResponse, BackgroundJobResponse)
from fastapi import APIRouter, Depends, HTTPException, Header, Query, Request
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
import datetime as dt
import hashlib
import json
import os

from ..runtime import log
from ..deps import _actor_id, _idempotency_key

router = APIRouter()


@router.post("/api/integrations/shopify/authorize",
          response_model=ShopifyAuthorizationResponse)
def shopify_authorize(shop: str, db: Session = Depends(get_session)) -> ShopifyAuthorizationResponse:
    store = crud.get_or_create_dev_store(db)
    try:
        url = shopify.create_authorization(
            db, store_id=store.id, actor_id=_actor_id(store.user_id), shop=shop
        )
        return ShopifyAuthorizationResponse(authorization_url=url)
    except (shopify.ShopifyAuthorizationError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/integrations/shopify/account",
         response_model=ShopifyConnectionResponse)
def shopify_account(db: Session = Depends(get_session)) -> ShopifyConnectionResponse:
    store = crud.get_or_create_dev_store(db)
    channel = db.scalar(select(models.ChannelConnection).where(
        models.ChannelConnection.store_id == store.id,
        models.ChannelConnection.provider == "shopify",
        models.ChannelConnection.status == "connected",
        models.ChannelConnection.external_account_id.like("%.myshopify.com"),
    ).order_by(models.ChannelConnection.synced_at.desc()))
    if channel is not None:
        provider = shopify.credential_provider(channel.external_account_id)
        if credentials.load_credential(db, store_id=store.id, provider=provider):
            return _shopify_connection_response(channel)

    # A connection that was revoked must not simply vanish from the screen. It is
    # true that the shop is no longer connected, but "connect a store" with no
    # explanation leaves the merchant to guess why the one they had disappeared.
    revoked = db.scalar(select(models.ChannelConnection).where(
        models.ChannelConnection.store_id == store.id,
        models.ChannelConnection.provider == "shopify",
        models.ChannelConnection.status == "disconnected",
        models.ChannelConnection.external_account_id.like("%.myshopify.com"),
    ).order_by(models.ChannelConnection.synced_at.desc()))
    if revoked is not None and (revoked.settings or {}).get("revoked_reason"):
        return ShopifyConnectionResponse(
            connected=False, shop=revoked.external_account_id,
            notifications="pending",
            reason=(revoked.settings or {})["revoked_reason"],
        )
    return ShopifyConnectionResponse(connected=False)


@router.post("/api/integrations/shopify/notifications/retry",
          response_model=ShopifyConnectionResponse)
def shopify_retry_notifications(db: Session = Depends(get_session)) -> ShopifyConnectionResponse:
    """Re-subscribe a connected shop to order notifications after a failed attempt."""
    store = crud.get_or_create_dev_store(db)
    channel = db.scalar(select(models.ChannelConnection).where(
        models.ChannelConnection.store_id == store.id,
        models.ChannelConnection.provider == "shopify",
        models.ChannelConnection.status == "connected",
        models.ChannelConnection.external_account_id.like("%.myshopify.com"),
    ).order_by(models.ChannelConnection.synced_at.desc()))
    if channel is None:
        raise HTTPException(status_code=409, detail="No Shopify shop is connected")
    shop = channel.external_account_id
    access_token = credentials.load_credential(
        db, store_id=store.id, provider=shopify.credential_provider(shop)
    )
    if access_token is None:
        raise HTTPException(status_code=409, detail="Shopify credentials are missing; reconnect the shop")
    channel.settings = _register_shopify_webhooks(
        channel.settings, shop=shop, access_token=access_token
    )
    db.add(channel)
    db.commit()
    db.refresh(channel)
    result = _shopify_connection_response(channel)
    crud.append_audit_event(
        db, store_id=store.id, actor_id=_actor_id(store.user_id),
        action="shopify.notifications.retry", resource_type="channel_connection",
        resource_id=str(channel.id),
        after={"notifications": result.notifications, "topics": result.topics},
    )
    return result


@router.post("/api/integrations/shopify/sync", response_model=BackgroundJobResponse)
def shopify_sync(days: int = Query(default=30, ge=1, le=60),
                 db: Session = Depends(get_session),
                 idempotency_key: str | None = Header(
                     default=None, alias="Idempotency-Key")) -> BackgroundJobResponse:
    if not queue_enabled():
        raise HTTPException(status_code=503, detail="Background queue is required for Shopify sync")
    store = crud.get_or_create_dev_store(db)
    # Checked here rather than only in the screen that offers it: a limit that
    # exists in a browser is not a limit.
    entitlement.require_access(db, store=store)
    channel = db.scalar(select(models.ChannelConnection).where(
        models.ChannelConnection.store_id == store.id,
        models.ChannelConnection.provider == "shopify",
        models.ChannelConnection.status == "connected",
        models.ChannelConnection.external_account_id.like("%.myshopify.com"),
    ))
    if channel is None:
        raise HTTPException(status_code=409, detail="Shopify store is not connected")
    record, is_new = crud.claim_idempotency(
        db, store_id=store.id, operation=f"shopify.sync:{channel.id}:{days}",
        key=_idempotency_key(idempotency_key),
    )
    if not is_new:
        status = "finished" if record.status == "completed" else record.status
        return BackgroundJobResponse(job_id=str(record.id), status=status, result=record.response)
    try:
        from ..queueing import enqueue_shopify_sync
        job_id = enqueue_shopify_sync(
            job_id=str(record.id), tenant_id=str(store.id),
            actor_id=str(_actor_id(store.user_id)), channel_id=str(channel.id), days=days,
        )
        return BackgroundJobResponse(job_id=job_id, status="queued")
    except Exception as exc:
        crud.fail_idempotency(db, record)
        log.exception("Failed to enqueue Shopify sync")
        raise HTTPException(status_code=503, detail="Background queue unavailable") from exc


# How long a delivery may sit in "processing" before we conclude the attempt died.
# Shopify gives a webhook five seconds to answer, so anything still processing
# after this was not slow — its process is gone.
#: How long a delivery may sit in `processing` before the process holding it is
#: presumed dead and the row may be reclaimed. This handler does a few queries
#: and finishes in under a second, so two minutes is generous; the old five
#: minutes only meant a killed container's row stayed unreclaimable for longer.
STALE_PROCESSING_SECONDS = int(os.getenv("WEBHOOK_STALE_PROCESSING_SECONDS", "120"))


def _is_retryable_delivery(existing: models.WebhookDelivery) -> bool:
    """May this retry be processed, or is it a genuine duplicate?

    Two kinds of unfinished delivery must be reclaimed, not dismissed:

    - `failed`: the handler caught something and said so.
    - `processing` that has gone stale: the handler never got to say anything,
      because the process died mid-flight. This is the more likely of the two —
      a caught failure at least has code to record itself — and the more
      dangerous, because the row looks exactly like a delivery in progress.

    A delivery still `processing` *recently* is not reclaimed here, because a
    concurrent attempt may still be running and processing it twice would
    double-count an order. It is not a duplicate either: the caller answers such
    a retry with a retryable status rather than 200, so Shopify comes back
    instead of concluding the event was handled.
    """
    if existing.status == "failed":
        return True
    if existing.status != "processing":
        return False
    started = existing.received_at
    if started is None:
        return True
    if started.tzinfo is None:
        started = started.replace(tzinfo=dt.timezone.utc)
    age = (dt.datetime.now(dt.timezone.utc) - started).total_seconds()
    if age > STALE_PROCESSING_SECONDS:
        log.warning("Reclaiming Shopify delivery %s stuck processing for %.0fs",
                    existing.delivery_id, age)
        return True
    return False


def _register_shopify_webhooks(settings: dict | None, *, shop: str,
                               access_token: str) -> dict:
    """Subscribe the shop to order notifications, recording why if it fails.

    Never raises: a shop that connects but cannot deliver notifications is still
    usable through manual sync, so the connection is kept and the reason stored
    for the retry endpoint to surface.
    """
    settings = dict(settings or {})
    callback_uri = os.getenv("SHOPIFY_WEBHOOK_URI")
    if not callback_uri:
        return {**settings, "webhooks": [], "webhook_setup": "pending_configuration",
                "webhook_error": "SHOPIFY_WEBHOOK_URI is not configured."}
    client = shopify.AdminGraphQLClient(shop, access_token)
    try:
        topics = client.ensure_webhook_subscriptions(callback_uri)
    except shopify.ShopifyAPIError as exc:
        log.warning("Shopify webhook registration failed for %s: %s", shop, exc)
        return {**settings, "webhooks": [], "webhook_setup": "pending_configuration",
                "webhook_error": str(exc)[:500]}
    finally:
        client.close()
    return {**settings, "webhooks": topics, "webhook_setup": "active",
            "webhook_error": None, "webhook_uri": callback_uri}


def _shopify_connection_response(channel: models.ChannelConnection) -> ShopifyConnectionResponse:
    """Report notifications as active only if they point where we now listen.

    A development tunnel gets a new hostname on every restart. The subscriptions
    survive that, aimed at an address nobody answers, and Shopify retries into the
    void without telling anyone. Comparing what was registered against what is
    configured is the only way that failure ever becomes visible.
    """
    settings = channel.settings or {}
    topics = settings.get("webhooks") or []
    registered_uri = settings.get("webhook_uri")
    configured_uri = os.getenv("SHOPIFY_WEBHOOK_URI")
    reason = settings.get("webhook_error")
    # A rotated SHOPIFY_CLIENT_SECRET fails at the signature check and nowhere
    # else: the subscriptions still exist and still point here, so every other
    # test passes while orders stop arriving. Checked before them, because a
    # delivery being refused makes the rest academic.
    if webhook_health.signatures_failing():
        return ShopifyConnectionResponse(
            connected=True, shop=channel.external_account_id,
            channel_id=str(channel.id), notifications="failing_signature",
            topics=topics,
            reason=("Shopify's notifications are arriving and being refused: their "
                    "signature does not match SHOPIFY_CLIENT_SECRET. After rotating "
                    "that secret, both Shopify and this deployment have to hold the "
                    "new one. See ops/SECRETS.md."),
        )
    if not topics:
        status = "pending"
    elif configured_uri and registered_uri and registered_uri != configured_uri:
        status = "stale"
        reason = (f"Notifications still point at {registered_uri}, but this server now "
                  f"listens at {configured_uri}. Turn them on again to move them.")
    else:
        status = "active"
    return ShopifyConnectionResponse(
        connected=True, shop=channel.external_account_id, channel_id=str(channel.id),
        notifications=status, topics=topics, reason=reason,
    )


@router.get("/api/integrations/shopify/callback",
         response_model=ShopifyConnectionResponse)
def shopify_callback(request: Request, db: Session = Depends(get_session)) -> ShopifyConnectionResponse:
    try:
        query = shopify.verify_callback_query(request.scope.get("query_string", b""))
        shop = shopify.normalize_shop(query.get("shop", ""))
        state = shopify.consume_state(db, query.get("state", ""), shop)
        # Asked before the tenant is declared, and that ordering is the point.
        # This is the one question here that has to see across tenants — whether
        # somebody else already holds this shop — and row-level security answers
        # it with "no rows" once app.tenant_id is set, which reads as "nobody
        # does". Asking first is what keeps one shop from being connected twice.
        existing = list(db.scalars(select(models.ChannelConnection).where(
            models.ChannelConnection.provider == "shopify",
            models.ChannelConnection.external_account_id == shop,
        )))
        if any(row.store_id != state.store_id for row in existing):
            raise shopify.ShopifyAuthorizationError(
                "This Shopify shop is already connected to another tenant.")
        # The state row is how this callback learns whose store it is.
        declare_tenant(db, state.store_id)
        token = shopify.exchange_code(shop, query.get("code", ""))
        channel = next(iter(existing), None) or models.ChannelConnection(
            store_id=state.store_id, provider="shopify", external_account_id=shop,
            display_name=shop.removesuffix(".myshopify.com"), status="connected",
            currency="USD", settings={"scopes": token.get("scope", "").split(",")},
        )
        channel.status = "connected"
        channel.synced_at = dt.datetime.now(dt.timezone.utc)
        channel.settings = _register_shopify_webhooks(
            channel.settings, shop=shop, access_token=token["access_token"]
        )
        # Keep the channel and credential in one transaction. If encryption or
        # the credential insert fails, no misleading `connected` row survives.
        db.add(channel); db.flush()
        credentials.store_credential(
            db, store_id=state.store_id, provider=shopify.credential_provider(shop),
            secret=token["access_token"],
        )
        # store_credential commits its transaction. PostgreSQL's tenant setting
        # is transaction-local, so the audit write must declare it again.
        declare_tenant(db, state.store_id)
        crud.append_audit_event(
            db, store_id=state.store_id, actor_id=state.actor_id,
            action="shopify.connected", resource_type="channel_connection",
            resource_id=str(channel.id), after={"shop": shop}, commit=False,
        )
        db.commit()
        return ShopifyConnectionResponse(
            connected=True, shop=shop, channel_id=str(channel.id)
        )
    except shopify.ShopifyAuthorizationError as exc:
        raise HTTPException(status_code=400, detail="Shopify authorization failed") from exc
    except Exception as exc:
        log.exception("Shopify OAuth callback failed")
        raise HTTPException(status_code=502, detail="Shopify authorization failed") from exc


@router.post("/api/webhooks/shopify")
async def shopify_webhook(request: Request, db: Session = Depends(get_session)) -> dict[str, str]:
    raw_body = await request.body()
    if len(raw_body) > 2 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Webhook payload too large")
    if not shopify.verify_webhook(raw_body, request.headers.get("X-Shopify-Hmac-Sha256")):
        # Counted, because a rotated SHOPIFY_CLIENT_SECRET fails exactly here and
        # nowhere visible: the subscriptions still exist, the screen still says
        # active, and orders simply stop arriving.
        webhook_health.record_rejected()
        raise HTTPException(status_code=401, detail="Invalid webhook signature")
    webhook_health.record_accepted()
    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid webhook JSON") from exc
    shop = shopify.normalize_shop(request.headers.get("X-Shopify-Shop-Domain", ""))
    topic = request.headers.get("X-Shopify-Topic", "")[:64]
    delivery_id = request.headers.get("X-Shopify-Webhook-Id", "")[:128]
    if not topic or not delivery_id:
        raise HTTPException(status_code=400, detail="Webhook metadata is missing")
    channels = list(db.scalars(select(models.ChannelConnection).where(
        models.ChannelConnection.provider == "shopify",
        models.ChannelConnection.external_account_id == shop,
    )))
    if len(channels) != 1:
        raise HTTPException(status_code=404, detail="Shopify channel not found")
    channel = channels[0]
    # The lookup above is how this request finds out whose store it is. Now that
    # it knows, every write below is scoped like any other tenant's work.
    declare_tenant(db, channel.store_id)
    payload_hash = hashlib.sha256(raw_body).hexdigest()
    delivery = models.WebhookDelivery(
        store_id=channel.store_id, provider="shopify", delivery_id=delivery_id,
        topic=topic, payload_hash=payload_hash, status="processing",
    )
    db.add(delivery)
    try:
        # Keep the receipt and its derived metrics atomic. A flush still takes
        # the unique-delivery lock, while avoiding a new RLS-less transaction
        # after commit/refresh. If the process dies, PostgreSQL rolls everything
        # back and Shopify's retry can safely start fresh.
        db.flush()
    except IntegrityError:
        db.rollback()
        declare_tenant(db, channel.store_id)
        existing = db.scalar(select(models.WebhookDelivery).where(
            models.WebhookDelivery.provider == "shopify",
            models.WebhookDelivery.delivery_id == delivery_id,
        ))
        if existing is not None and existing.payload_hash != payload_hash:
            log.error("Shopify reused webhook id %s with a different payload", delivery_id)
            raise HTTPException(status_code=409, detail="Webhook delivery payload mismatch")
        if existing is not None and _is_retryable_delivery(existing):
            # Shopify retries with the same webhook id. Reclaim the row instead of
            # answering "duplicate", which would tell Shopify we handled an event
            # we did not and lose it for good.
            existing.status = "processing"
            existing.processed_at = None
            existing.error_code = None
            existing.received_at = dt.datetime.now(dt.timezone.utc)
            db.add(existing); db.flush()
            delivery = existing
        elif existing is not None and existing.status == "completed":
            return {"status": "duplicate"}
        elif existing is not None:
            # Unfinished, and too young to reclaim: another attempt may still be
            # in flight, and running this one alongside it would count the order
            # twice. Answering 200 would end the matter for good — Shopify stops
            # retrying a delivery it believes was handled, so if that other
            # attempt never finishes the event is lost with nothing marked
            # failed anywhere. That is exactly how order #1002 disappeared.
            # A retryable status keeps Shopify coming back until the row is
            # either completed (a real duplicate) or stale enough to reclaim.
            log.warning("Shopify delivery %s is still unfinished; asking for a retry",
                        delivery_id)
            raise HTTPException(status_code=409, detail="Delivery already in progress")
        else:
            raise
    try:
        now = dt.datetime.now(dt.timezone.utc)
        if topic == "orders/create":
            created = dt.datetime.fromisoformat(
                str(payload.get("created_at", "")).replace("Z", "+00:00")
            ).date()
            row = db.scalar(select(models.CommerceDailyMetric).where(
                models.CommerceDailyMetric.store_id == channel.store_id,
                models.CommerceDailyMetric.channel_id == channel.id,
                models.CommerceDailyMetric.metric_date == created,
            )) or models.CommerceDailyMetric(
                store_id=channel.store_id, channel_id=channel.id, metric_date=created,
                revenue=0, refunds=0, fees=0, landed_cogs=0, advertising_spend=0,
                orders=0, units=0, sessions=0, currency=payload.get("currency") or channel.currency,
                source="shopify_webhook", costs_complete=False,
            )
            row.revenue = float(row.revenue) + float(payload.get("total_price") or 0)
            row.orders += 1
            row.units += sum(int(item.get("quantity") or 0)
                             for item in payload.get("line_items") or [])
            row.synced_at = now
            db.add(row)
        elif topic == "refunds/create":
            created = dt.datetime.fromisoformat(
                str(payload.get("created_at", "")).replace("Z", "+00:00")
            ).date()
            successful = [item for item in payload.get("transactions") or []
                          if item.get("kind") == "refund" and item.get("status") == "success"]
            refund_amount = sum(float(item.get("amount") or 0) for item in successful)
            if refund_amount:
                row = db.scalar(select(models.CommerceDailyMetric).where(
                    models.CommerceDailyMetric.store_id == channel.store_id,
                    models.CommerceDailyMetric.channel_id == channel.id,
                    models.CommerceDailyMetric.metric_date == created,
                )) or models.CommerceDailyMetric(
                    store_id=channel.store_id, channel_id=channel.id, metric_date=created,
                    revenue=0, refunds=0, fees=0, landed_cogs=0, advertising_spend=0,
                    orders=0, units=0, sessions=0,
                    currency=(successful[0].get("currency") or channel.currency),
                    source="shopify_webhook", costs_complete=False,
                )
                row.refunds = float(row.refunds) + refund_amount
                row.synced_at = now; db.add(row)
        elif topic == "app/uninstalled":
            channel.status = "disconnected"; channel.synced_at = now; db.add(channel)
        elif topic == "shop/redact":
            # The product stores no customer/order payloads. Remove derived shop facts
            # and revoke the locally retained credential when Shopify requests erasure.
            db.execute(delete(models.CommerceDailyMetric).where(
                models.CommerceDailyMetric.store_id == channel.store_id,
                models.CommerceDailyMetric.channel_id == channel.id,
            ))
            db.execute(delete(models.IntegrationCredential).where(
                models.IntegrationCredential.store_id == channel.store_id,
                models.IntegrationCredential.provider == shopify.credential_provider(shop),
            ))
            channel.status = "disconnected"
            channel.settings = {**(channel.settings or {}), "privacy_erased_at": now.isoformat()}
            channel.synced_at = now; db.add(channel)
        elif topic in {"customers/data_request", "customers/redact"}:
            # No customer identity or raw order payload is persisted, so there is
            # nothing customer-specific to export or erase.
            pass
        delivery.status = "completed"; delivery.processed_at = now; db.add(delivery); db.commit()
        return {"status": "accepted"}
    except Exception as exc:
        db.rollback()
        declare_tenant(db, channel.store_id)
        failed = db.scalar(select(models.WebhookDelivery).where(
            models.WebhookDelivery.provider == "shopify",
            models.WebhookDelivery.delivery_id == delivery_id,
        ))
        if failed is not None:
            failed.status = "failed"; failed.error_code = type(exc).__name__[:64]
            failed.processed_at = dt.datetime.now(dt.timezone.utc)
            db.add(failed); db.commit()
        raise HTTPException(status_code=500, detail="Webhook processing failed") from exc
