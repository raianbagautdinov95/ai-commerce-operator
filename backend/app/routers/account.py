"""Subscription state, checkout and the Stripe webhook."""

from .. import billing
from ..runtime import log
from .. import stripe_billing
from ..db import crud, models
from ..db.session import declare_tenant, get_session
from .. import entitlement
from .. import notifications
from .. import onboarding
from ..schemas import (AccountStatusResponse, BillingLinkRequest,
                       BillingLinkResponse, EntitlementResponse,
                       OnboardingResponse)
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
import datetime as dt
import hashlib
import json
import uuid

router = APIRouter()


@router.get("/api/account/status", response_model=AccountStatusResponse)
def account_status(db: Session = Depends(get_session)) -> AccountStatusResponse:
    store = crud.get_or_create_dev_store(db)
    subscription = billing.ensure_subscription(db, store_id=store.id)
    now = dt.datetime.now(dt.timezone.utc)
    trial_end = subscription.trial_ends_at
    if trial_end.tzinfo is None: trial_end = trial_end.replace(tzinfo=dt.timezone.utc)
    days_remaining = int((max(0.0, (trial_end - now).total_seconds()) + 86399) // 86400)
    connected = db.scalar(select(models.ChannelConnection.id).where(
        models.ChannelConnection.store_id == store.id,
        models.ChannelConnection.status == "connected",
        ~models.ChannelConnection.external_account_id.like("DEMO-%"),
    ).limit(1)) is not None
    imported = db.scalar(select(models.CommerceDailyMetric.id).where(
        models.CommerceDailyMetric.store_id == store.id,
        models.CommerceDailyMetric.source.not_like("demo_%"),
    ).limit(1)) is not None
    acted = db.scalar(select(models.ProductEvaluation.id).where(
        models.ProductEvaluation.store_id == store.id,
    ).limit(1)) is not None
    onboarding = {"account_created": True, "channel_connected": connected,
                  "data_imported": imported, "first_ai_action": acted}
    return AccountStatusResponse(
        plan=subscription.plan, subscription_status=subscription.status,
        trial_ends_at=subscription.trial_ends_at, trial_days_remaining=days_remaining,
        entitlements=billing.entitlement(subscription.plan), onboarding=onboarding,
        completion_percentage=round(sum(onboarding.values()) / len(onboarding) * 100),
    )


@router.get("/api/billing", response_model=EntitlementResponse)
def billing_state(db: Session = Depends(get_session)) -> EntitlementResponse:
    """The one place the answer is decided. The screen renders it; it does not
    recompute it from dates or from Stripe's wording."""
    store = crud.get_or_create_dev_store(db)
    state = entitlement.current(db, store=store)
    return EntitlementResponse(
        **state.to_dict(), features=list(billing.PILOT_FEATURES),
        **{k: v for k, v in onboarding.support().items()
           if k in {"support_email", "support_response_time"}},
    )


@router.get("/api/onboarding", response_model=OnboardingResponse)
def onboarding_status(db: Session = Depends(get_session)) -> OnboardingResponse:
    """Where this customer actually is, read from their own rows.

    Nothing is accepted from the caller and there is no way to mark a step done:
    a checklist that believes the browser will report a shop as connected whose
    token was never stored.
    """
    store = crud.get_or_create_dev_store(db)
    return OnboardingResponse(**onboarding.status(db, store=store).to_dict())


@router.post("/api/billing/checkout", response_model=BillingLinkResponse)
def billing_checkout(req: BillingLinkRequest, db: Session = Depends(get_session)) \
        -> BillingLinkResponse:
    store = crud.get_or_create_dev_store(db)
    subscription = billing.ensure_subscription(db, store_id=store.id)
    user = db.get(models.User, store.user_id)
    try:
        url = stripe_billing.create_checkout(
            store_id=str(store.id), email=user.email,
            plan=req.plan, customer=subscription.stripe_customer_id,
        )
        return BillingLinkResponse(url=url)
    except (stripe_billing.StripeConfigurationError, stripe_billing.StripeAPIError) as exc:
        raise HTTPException(status_code=503, detail="Stripe billing is not configured") from exc


@router.post("/api/billing/portal", response_model=BillingLinkResponse)
def billing_portal(db: Session = Depends(get_session)) -> BillingLinkResponse:
    store = crud.get_or_create_dev_store(db)
    subscription = billing.ensure_subscription(db, store_id=store.id)
    if not subscription.stripe_customer_id:
        raise HTTPException(status_code=409, detail="No Stripe customer exists for this workspace")
    try:
        return BillingLinkResponse(url=stripe_billing.create_portal(
            customer=subscription.stripe_customer_id
        ))
    except (stripe_billing.StripeConfigurationError, stripe_billing.StripeAPIError) as exc:
        raise HTTPException(status_code=503, detail="Stripe billing is unavailable") from exc


@router.post("/api/webhooks/stripe")
async def stripe_webhook(request: Request, db: Session = Depends(get_session)) -> dict[str, str]:
    raw = await request.body()
    if len(raw) > 512 * 1024:
        raise HTTPException(status_code=413, detail="Webhook payload too large")
    try:
        stripe_billing.verify_event(raw, request.headers.get("Stripe-Signature"))
        event = json.loads(raw)
    except (ValueError, json.JSONDecodeError, stripe_billing.StripeConfigurationError) as exc:
        raise HTTPException(status_code=401, detail="Invalid Stripe webhook") from exc
    event_id = str(event.get("id") or "")[:128]
    event_type = str(event.get("type") or "")[:64]
    if not event_id or not event_type:
        raise HTTPException(status_code=400, detail="Stripe webhook metadata is missing")
    obj = ((event.get("data") or {}).get("object") or {})
    metadata = obj.get("metadata") or {}
    row = None
    store_id = metadata.get("store_id") or obj.get("client_reference_id")
    if store_id:
        # Declared before the lookup, not after. `subscriptions` is under FORCE
        # row-level security and this endpoint is public, so nothing is carrying
        # a tenant into the transaction: without this the query matches no rows
        # and every Stripe event 404s, leaving billing status frozen at whatever
        # it was while Stripe believes it has been told.
        #
        # Checkout stamps subscription_data[metadata][store_id], so every later
        # subscription event carries it too.
        try:
            declare_tenant(db, uuid.UUID(str(store_id)))
            row = db.scalar(select(models.Subscription).where(
                models.Subscription.store_id == uuid.UUID(str(store_id))))
        except ValueError:
            row = None
    if row is None and obj.get("id"):
        # No tenant to declare, so RLS answers this with nothing — which is the
        # safe direction. It only arises for a subscription created outside our
        # own Checkout, which stamps the metadata above.
        row = db.scalar(select(models.Subscription).where(
            models.Subscription.stripe_subscription_id == str(obj["id"])))
    if row is None:
        raise HTTPException(status_code=404, detail="Stripe subscription was not found")
    declare_tenant(db, row.store_id)
    delivery = models.WebhookDelivery(
        store_id=row.store_id, provider="stripe", delivery_id=event_id, topic=event_type,
        payload_hash=hashlib.sha256(raw).hexdigest(), status="processing",
    )
    db.add(delivery)
    # Flushed, not committed. Committing the receipt before doing the work means
    # a process that dies in between leaves a row saying `processing` for ever,
    # and Stripe's retry is then answered "duplicate" — a 200 — so the event is
    # dropped with nothing marked failed anywhere. That is exactly how a Shopify
    # order was lost here. One transaction: either the receipt and the change
    # both land, or neither does and the retry starts fresh.
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        declare_tenant(db, row.store_id)
        existing = db.scalar(select(models.WebhookDelivery).where(
            models.WebhookDelivery.provider == "stripe",
            models.WebhookDelivery.delivery_id == event_id,
        ))
        # A completed delivery is a genuine duplicate. An unfinished one is a
        # retry of something that never landed, and answering 200 would end it.
        if existing is not None and existing.status == "completed":
            return {"status": "duplicate"}
        raise HTTPException(status_code=409, detail="Delivery already in progress")
    now = dt.datetime.now(dt.timezone.utc)
    # Remembered before it changes: an email goes out when the state actually
    # moved, not every time Stripe mentions a subscription that was already
    # active.
    previous_status = row.status
    if event_type == "checkout.session.completed":
        # The customer and the subscription are worth recording either way: they
        # are how the Portal is opened and how a later event finds this row.
        row.stripe_customer_id = str(obj.get("customer") or "") or row.stripe_customer_id
        row.stripe_subscription_id = str(obj.get("subscription") or "") or row.stripe_subscription_id
        row.plan = metadata.get("plan") if metadata.get("plan") in billing.PLANS else row.plan
        # Access is not one of them. A subscription Checkout completes even when
        # the money has not arrived — a card needing authentication, or one that
        # failed after submission — and the subscription is then `incomplete`.
        # Stripe sends customer.subscription.updated if it settles, and that is
        # what turns this on. Activating here would hand over the product to
        # somebody whose payment never landed.
        if stripe_billing.checkout_is_paid(obj):
            row.status = "active"
        else:
            log.warning("Stripe checkout %s completed unpaid; leaving access as %s",
                        event_id, row.status)
    elif event_type in {"customer.subscription.updated", "customer.subscription.deleted"}:
        stripe_status = str(obj.get("status") or "canceled")
        row.status = {"active": "active", "trialing": "trialing", "past_due": "past_due",
                      "unpaid": "past_due", "canceled": "canceled"}.get(stripe_status, "inactive")
        row.plan = metadata.get("plan") if metadata.get("plan") in billing.PLANS else row.plan
        # Read from wherever this payload carries it. Stripe reports the period
        # on the subscription in some API versions and on its items in others,
        # and reading one place meant the date a customer is shown quietly
        # stopped moving the day their account was upgraded.
        ends_at = stripe_billing.period_end(obj)
        if ends_at:
            row.current_period_end = dt.datetime.fromtimestamp(ends_at, dt.timezone.utc)
        else:
            # Not told is not expired. Left alone on purpose, and said out loud
            # rather than passed over, because silence is how this went unnoticed.
            log.warning("Stripe %s carried no period end for subscription %s",
                        event_type, row.id)
    row.updated_at = now; delivery.status = "completed"; delivery.processed_at = now
    db.add_all([row, delivery])

    # Reserved inside the same transaction that records the state change, so a
    # redelivered event finds the receipt and sends nothing. The send itself is
    # a queued job: Stripe wants an answer in seconds and an SMTP round trip is
    # not something to make it wait for.
    #
    # Keyed on the Stripe event id, which is what makes two deliveries the same
    # delivery. A genuinely new incident carries a new event id and so earns a
    # new email.
    kind = {
        "active": notifications.ACTIVATED,
        "past_due": notifications.PAYMENT_ATTENTION,
        "canceled": notifications.CANCELED,
    }.get(row.status)
    receipt = None
    if kind is not None and previous_status != row.status:
        receipt = notifications.reserve(
            db, store_id=row.store_id, kind=kind, dedupe_key=event_id)
    db.commit()

    if receipt is not None:
        try:
            from ..queueing import enqueue_notification, queue_enabled
            if queue_enabled():
                enqueue_notification(receipt_id=str(receipt.id),
                                     tenant_id=str(row.store_id), kind=kind,
                                     context={})
        except Exception:  # noqa: BLE001 - Stripe must not retry over an email
            log.exception("Could not queue the %s notification", kind)
    return {"status": "accepted"}
