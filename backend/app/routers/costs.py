"""What each product costs, and which results are waiting on the answer.

The screen behind this exists because "add a cost" is not actionable on its own.
A seller needs to know which product, which variant, what it is worth to them —
the measurement that cannot finish without it — and what is already on record,
including where that figure came from.

Nothing here computes money. It stores what the seller says, reports what
Shopify said, and names the measurements that are stuck.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import actions_engine, commerce_measurement, entitlement, product_costs
from ..db import crud, models
from ..db.session import get_session
from ..deps import _actor_id
from ..schemas import (ProductCostEntry, ProductCostHistoryOut, ProductCostOut,
                       ProductCostsResponse, ProductCostWrite)

router = APIRouter()

#: How far back to look for something that has actually sold. A product nobody
#: has sold in three months is not what somebody opened this screen for.
RECENT_DAYS = 90


def _sold_variants(db: Session, store_id, *, since: dt.date) -> dict[tuple[str, str], dict]:
    rows = db.scalars(select(models.VariantDailyMetric).where(
        models.VariantDailyMetric.store_id == store_id,
        models.VariantDailyMetric.metric_date >= since,
    ))
    seen: dict[tuple[str, str], dict] = {}
    for row in rows:
        key = (row.external_product_id, row.external_variant_id)
        entry = seen.setdefault(key, {"units": 0, "title": row.variant_title,
                                      "currency": row.currency,
                                      "last_sold": row.metric_date})
        entry["units"] += int(row.units or 0) - int(row.refunded_units or 0)
        entry["title"] = entry["title"] or row.variant_title
        entry["last_sold"] = max(entry["last_sold"], row.metric_date)
    return seen


def _product_titles(db: Session, store_id) -> dict[str, str]:
    return {row.external_product_id: (row.title or "")
            for row in db.scalars(select(models.ChannelProduct).where(
                models.ChannelProduct.store_id == store_id))}


def _waiting_on_cost(db: Session, store_id, *, now: dt.datetime) -> dict[str, list[str]]:
    """Which applied actions cannot be priced, keyed by product.

    A cost with nothing waiting on it is housekeeping. A cost with a measurement
    behind it is the difference between a result and a blank.
    """
    waiting: dict[str, list[str]] = {}
    rows = db.scalars(select(models.OperatorAction).where(
        models.OperatorAction.store_id == store_id,
        models.OperatorAction.status == actions_engine.ActionStatus.APPLIED.value,
        models.OperatorAction.measured_at.is_(None),
    ).limit(100))
    for row in rows:
        product_id = str((row.baseline or {}).get("product_id") or "")
        if not product_id:
            continue
        state = commerce_measurement.readiness(db, row, now=now)
        if state.state in (commerce_measurement.AWAITING_COST,
                           commerce_measurement.WINDOW_OPEN,
                           commerce_measurement.READY):
            waiting.setdefault(product_id, []).append(row.target)
    return waiting


@router.get("/api/product-costs", response_model=ProductCostsResponse)
def list_product_costs(db: Session = Depends(get_session)) -> ProductCostsResponse:
    store = crud.get_or_create_dev_store(db)
    entitlement.require_access(db, store=store)
    now = dt.datetime.now(dt.timezone.utc)
    today = now.date()
    since = today - dt.timedelta(days=RECENT_DAYS)

    titles = _product_titles(db, store.id)
    waiting = _waiting_on_cost(db, store.id, now=now)
    sold = _sold_variants(db, store.id, since=since)

    entries: list[ProductCostEntry] = []
    for (product_id, variant_id), fact in sorted(
            sold.items(), key=lambda item: -item[1]["units"]):
        cost = product_costs.cost_on(db, store_id=store.id, product_id=product_id,
                                     variant_id=variant_id, on=today)
        entries.append(ProductCostEntry(
            product_id=product_id,
            product_title=titles.get(product_id) or product_id,
            variant_id=variant_id if not product_costs.is_unknown_variant(variant_id)
            else None,
            variant_title=fact["title"],
            units_sold=fact["units"],
            last_sold=fact["last_sold"],
            sale_currency=fact["currency"],
            unit_cost=float(cost.amount) if cost else None,
            cost_currency=cost.currency if cost else None,
            effective_from=cost.effective_from if cost else None,
            source=cost.source if cost else None,
            verification=cost.verification if cost else None,
            applies_to_every_variant=(cost is not None and not cost.exact_variant),
            currency_matches=(cost is None
                              or cost.currency.upper() == str(fact["currency"]).upper()),
            attributable_to_variant=not product_costs.is_unknown_variant(variant_id),
            waiting_measurements=waiting.get(product_id, []),
        ))

    missing = [entry for entry in entries if entry.unit_cost is None]
    blocked = [entry for entry in entries
               if entry.waiting_measurements
               and (entry.unit_cost is None or not entry.currency_matches
                    or not entry.attributable_to_variant)]
    return ProductCostsResponse(
        entries=entries, without_cost=len(missing), blocking=len(blocked),
        currency=next((entry.sale_currency for entry in entries), "USD"),
    )


@router.get("/api/product-costs/history", response_model=ProductCostHistoryOut)
def product_cost_history(product_id: str | None = None,
                         db: Session = Depends(get_session)) -> ProductCostHistoryOut:
    """Every value ever recorded, newest first. Nothing is overwritten, so this
    is the audit of a number that decides what the payroll may claim."""
    store = crud.get_or_create_dev_store(db)
    entitlement.require_access(db, store=store)
    rows = product_costs.history(db, store_id=store.id, product_id=product_id)
    return ProductCostHistoryOut(costs=[
        ProductCostOut(
            product_id=row.external_product_id,
            variant_id=row.external_variant_id or None,
            variant_title=row.variant_title,
            amount=float(row.amount),
            purchase_amount=(float(row.purchase_amount)
                             if row.purchase_amount is not None else None),
            extra_amount=(float(row.extra_amount)
                          if row.extra_amount is not None else None),
            currency=row.currency, effective_from=row.effective_from,
            source=row.source, verification=row.verification,
            entered_by=row.entered_by, note=row.note, observed_at=row.observed_at)
        for row in rows])


@router.post("/api/product-costs", response_model=ProductCostOut)
def set_product_cost(req: ProductCostWrite,
                     db: Session = Depends(get_session)) -> ProductCostOut:
    """Record what the seller says a unit costs them.

    Marked `reported`, not `confirmed`: nobody has checked it, and the screens
    say so. It still counts — the seller knows what they paid — but the claim
    that follows from it is theirs as much as ours.
    """
    store = crud.get_or_create_dev_store(db)
    entitlement.require_access(db, store=store)

    purchase = Decimal(str(req.purchase_amount))
    extra = Decimal(str(req.extra_amount or 0))
    if purchase < 0 or extra < 0:
        raise HTTPException(status_code=422,
                            detail="A cost cannot be negative.")
    total = purchase + extra
    effective_from = req.effective_from or dt.date.today()

    row = product_costs.record(
        db, store_id=store.id, product_id=req.product_id,
        variant_id=req.variant_id or "", variant_title=req.variant_title,
        amount=total, purchase_amount=purchase, extra_amount=extra,
        currency=req.currency, effective_from=effective_from,
        source=product_costs.MANUAL, verification=product_costs.REPORTED,
        entered_by=str(_actor_id(store.user_id)), note=req.note)
    if row is None:
        raise HTTPException(status_code=422,
                            detail="That is the same figure already on record.")
    crud.append_audit_event(
        db, store_id=store.id, actor_id=_actor_id(store.user_id),
        action="commerce.cost_recorded", resource_type="product_cost",
        resource_id=str(row.id),
        after={"product_id": req.product_id, "variant_id": req.variant_id or "",
               "amount": str(total), "currency": row.currency,
               "effective_from": effective_from.isoformat()},
        commit=False)
    db.commit(); db.refresh(row)
    return ProductCostOut(
        product_id=row.external_product_id, variant_id=row.external_variant_id or None,
        variant_title=row.variant_title, amount=float(row.amount),
        purchase_amount=float(row.purchase_amount) if row.purchase_amount is not None else None,
        extra_amount=float(row.extra_amount) if row.extra_amount is not None else None,
        currency=row.currency, effective_from=row.effective_from, source=row.source,
        verification=row.verification, entered_by=row.entered_by, note=row.note,
        observed_at=row.observed_at)
