"""Turning a store's own sales into proposals a human can decide on.

Until this existed, a seller could connect Shopify, sync real orders and watch
the proposals screen stay empty for ever: the metrics were stored and nothing
read them. The scan closes that gap, and deliberately does very little — it
reads one store's measured days, asks `commerce_engine` what they support
saying, and opens an action for each answer.

It never applies anything. `guardrails.DEFAULT_POLICY` sets `auto_apply_below`
to zero for a new store, and none of these actions could be applied by machine
anyway: entering landed costs or looking into refunds are things a person does.
Every proposal waits for a human.
"""
from __future__ import annotations

import datetime as dt
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import (actions_engine, commerce_engine, commerce_measurement,
                commerce_service, credentials, entitlement, shopify)
from ..db import crud, models
from ..db.session import get_session
from ..deps import _actor_id
from ..runtime import log
from ..routers.money import _action_out
from ..schemas import ActionOut, CommerceConfirmResponse, CommerceScanResponse

router = APIRouter()

#: Kept as an alias: the window itself is defined where the scan opens actions.
MEASUREMENT_DAYS = commerce_service.MEASUREMENT_DAYS


@router.post("/api/commerce/scan", response_model=CommerceScanResponse)
def commerce_scan(days: int = Query(default=30, ge=7, le=90),
                  db: Session = Depends(get_session)) -> CommerceScanResponse:
    """Read this store's measured days and open an action for each finding.

    The sync now does this by itself the moment it finishes, so this route is
    for a seller who has just changed something and does not want to wait.
    """
    store = crud.get_or_create_dev_store(db)
    entitlement.require_access(db, store=store)
    return CommerceScanResponse(**commerce_service.scan_store(db, store, days=days))


def _owned_commerce_action(db: Session, action_id: str,
                           store_id) -> models.OperatorAction:
    try:
        row = db.get(models.OperatorAction, uuid.UUID(action_id))
    except ValueError:
        row = None
    if row is None or row.store_id != store_id or row.module != "commerce":
        raise HTTPException(status_code=404, detail="Action not found")
    return row


def _shopify_channel(db: Session, store_id):
    channel = db.scalar(select(models.ChannelConnection).where(
        models.ChannelConnection.store_id == store_id,
        models.ChannelConnection.provider == "shopify",
        models.ChannelConnection.status == "connected",
    ))
    if channel is None:
        raise HTTPException(status_code=409, detail="No connected Shopify store")
    return channel


@router.post("/api/commerce/actions/{action_id}/confirm", response_model=CommerceConfirmResponse)
def confirm_commerce_action(action_id: str,
                            db: Session = Depends(get_session)) -> CommerceConfirmResponse:
    """Record that the seller did it — but only if the store agrees.

    This is the whole difference between a ledger and a diary. The seller says
    the restock happened; this reads the shelf back through Shopify's own API
    and refuses the claim if the number did not move. Nothing is taken on trust,
    and an action that cannot be witnessed cannot later be called proven.
    """
    store = crud.get_or_create_dev_store(db)
    row = _owned_commerce_action(db, action_id, store.id)
    if row.status != actions_engine.ActionStatus.PROPOSED.value:
        raise HTTPException(status_code=409, detail=f"Action is already {row.status}")
    if row.action_type not in actions_engine.VERIFIABLE_COMMERCE_ACTIONS:
        raise HTTPException(
            status_code=409,
            detail="This action cannot be witnessed through the Shopify API, "
                   "so it cannot be confirmed here.")

    channel = _shopify_channel(db, store.id)
    product_id = str((row.baseline or {}).get("product_id") or row.source_id or "")
    before = (row.baseline or {}).get("on_hand")
    if not product_id or before is None:
        raise HTTPException(status_code=409,
                            detail="This action has no product or stock level to compare against")

    token = credentials.load_credential(
        db, store_id=store.id,
        provider=shopify.credential_provider(channel.external_account_id))
    if not token:
        raise HTTPException(status_code=409, detail="Shopify credential is missing")
    client = shopify.AdminGraphQLClient(channel.external_account_id, token)
    try:
        now_on_hand = client.product_inventory(product_id)
    except shopify.ShopifyAPIError as exc:
        raise HTTPException(status_code=502, detail="Could not read the shelf back") from exc
    finally:
        client.close()

    if now_on_hand is None or now_on_hand <= int(before):
        return CommerceConfirmResponse(
            confirmed=False, on_hand_before=int(before), on_hand_now=now_on_hand,
            reason=("Shopify still reports "
                    f"{'no tracked stock' if now_on_hand is None else now_on_hand} "
                    f"against the {before} this was found at. Nothing was recorded — "
                    "the change has to be visible in the store before it counts."),
        )

    witnessed = dt.datetime.now(dt.timezone.utc)
    # The proof lives with the before-picture it disproves: same action, same
    # place, no second table to fall out of step with it.
    row.baseline = {**(row.baseline or {}), "verified_on_hand": int(now_on_hand),
                    "verified_at": witnessed.isoformat()}
    row.status = actions_engine.ActionStatus.APPLIED.value
    row.applied_by = "human"
    row.applied_at = witnessed
    db.add(row)
    crud.append_audit_event(
        db, store_id=store.id, actor_id=_actor_id(store.user_id),
        action="commerce.confirmed", resource_type="operator_action",
        resource_id=str(row.id),
        after={"on_hand_before": int(before), "on_hand_now": int(now_on_hand)},
        commit=False,
    )
    db.commit()
    return CommerceConfirmResponse(
        confirmed=True, on_hand_before=int(before), on_hand_now=int(now_on_hand),
        reason=f"Shopify now reports {now_on_hand}, up from {before}. Recorded.",
    )


@router.post("/api/commerce/actions/{action_id}/measure", response_model=ActionOut)
def measure_commerce_action(action_id: str,
                            db: Session = Depends(get_session)) -> ActionOut:
    """Price the result from the store's own days. Nobody types a number in.

    The arithmetic, the window and the evidence decision all live in
    `commerce_measurement`, which the daily job calls too. This route exists so
    a seller need not wait for tomorrow's run, not so there can be a second
    opinion — the same action measured by hand and by machine has to reach the
    same number or the number means nothing.

    It refuses before the window closes. A provisional reading was available
    here once, and it was a trap: pressing the button early recorded a result,
    which stopped the job from ever measuring the full window.
    """
    store = crud.get_or_create_dev_store(db)
    row = _owned_commerce_action(db, action_id, store.id)
    now = dt.datetime.now(dt.timezone.utc)

    # One transaction: a result that was recorded without its audit trail is a
    # number with nothing behind it, which is the one thing this table is for.
    result = commerce_measurement.measure(db, row, now=now, commit=False)
    if not result.measured:
        db.rollback()
        if result.state == commerce_measurement.MEASURED:
            return _action_out(row)
        raise HTTPException(status_code=409 if result.state ==
                            commerce_measurement.NOT_MEASURABLE else 422,
                            detail=result.reason)

    crud.append_audit_event(
        db, store_id=store.id, actor_id=_actor_id(store.user_id),
        action="commerce.measured", resource_type="operator_action",
        resource_id=str(row.id),
        after={"impact": row.impact, "evidence_mode": row.evidence_mode},
        commit=False,
    )
    db.commit(); db.refresh(row)
    return _action_out(row)
