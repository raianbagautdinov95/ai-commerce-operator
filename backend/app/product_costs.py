"""What a unit cost to buy, kept as history and never as a current value.

A measurement window is priced from the days inside it, so the cost that matters
is the one that was true on the day the unit sold. Overwriting last month's
figure with this month's would quietly restate every result that had already
been priced, and the seller would have no way of telling that it had happened.

So nothing here updates a row. A new value is a new row with its own
`effective_from`, and the lookup asks a question with a date in it.

Two sources, and the difference is kept visible rather than flattened:

  `shopify`  read out of the shop's own record (`InventoryItem.unitCost`),
             which `read_inventory` already permits — no new scope, no
             reconnection. Recorded as `confirmed`, meaning it came from the
             store rather than that anybody audited it.
  `manual`   a figure the seller typed. Recorded as `reported`, and every
             screen says so.

Amounts are Decimal from end to end. A margin computed in binary floating point
is a margin that disagrees with itself at the fourth sale.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import models

SHOPIFY = "shopify"
MANUAL = "manual"
CONFIRMED = "confirmed"
REPORTED = "reported"

#: Which source wins when two values are effective on the same day. A person
#: overriding what Shopify holds is a deliberate act; the reverse is a sync.
_SOURCE_RANK = {MANUAL: 1, SHOPIFY: 0}

#: The key used when a line item arrives without a variant. It is not a real
#: variant, so no cost will ever be found for it — which is the point: units we
#: cannot attribute to a variant are units we cannot price, and saying so is
#: better than pricing them from a neighbour's cost.
UNKNOWN_VARIANT_SUFFIX = "::unknown-variant"


def unknown_variant_key(product_id: str) -> str:
    return f"{product_id}{UNKNOWN_VARIANT_SUFFIX}"


def is_unknown_variant(variant_id: str) -> bool:
    return str(variant_id).endswith(UNKNOWN_VARIANT_SUFFIX)


@dataclass(frozen=True)
class Cost:
    """One cost, with enough provenance to argue about it."""
    amount: Decimal
    currency: str
    effective_from: dt.date
    source: str
    verification: str
    variant_id: str          # "" when it applies to every variant
    observed_at: dt.datetime

    @property
    def exact_variant(self) -> bool:
        return bool(self.variant_id)


def to_decimal(value) -> Decimal | None:
    """Money as Decimal, or nothing. A string that is not a number is not zero."""
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def record(db: Session, *, store_id, product_id: str, amount: Decimal,
           currency: str, effective_from: dt.date, source: str,
           verification: str, variant_id: str = "", variant_title: str | None = None,
           channel_id=None, entered_by: str | None = None,
           note: str | None = None, purchase_amount: Decimal | None = None,
           extra_amount: Decimal | None = None, commit: bool = False
           ) -> models.ProductCost | None:
    """Write one cost into the history, unless it says nothing new.

    Returns None when the newest value from this source is already the same:
    a daily sync that re-reads an unchanged cost must not fill the history with
    a row per day, or the history stops being readable as a history.

    Refuses a negative amount outright. Zero is allowed, because a shop may
    genuinely have paid nothing for a unit — but only as an explicit value, and
    never as a stand-in for one nobody supplied.
    """
    if amount is None or amount < 0:
        return None
    currency = (currency or "").upper()[:3]
    if not currency:
        return None

    latest = db.scalar(select(models.ProductCost).where(
        models.ProductCost.store_id == store_id,
        models.ProductCost.external_product_id == product_id,
        models.ProductCost.external_variant_id == (variant_id or ""),
        models.ProductCost.source == source,
    ).order_by(models.ProductCost.effective_from.desc(),
               models.ProductCost.observed_at.desc()))
    if (latest is not None
            and Decimal(str(latest.amount)) == amount
            and latest.currency == currency
            and latest.effective_from <= effective_from):
        return None

    existing = db.scalar(select(models.ProductCost).where(
        models.ProductCost.store_id == store_id,
        models.ProductCost.external_product_id == product_id,
        models.ProductCost.external_variant_id == (variant_id or ""),
        models.ProductCost.effective_from == effective_from,
        models.ProductCost.source == source,
    ))
    now = dt.datetime.now(dt.timezone.utc)
    if existing is not None:
        # Same day, same source, different figure: a correction, not history.
        # The day has not been used to price anything yet — measurement only
        # reads days that are over — so replacing it loses nothing.
        existing.amount = amount
        existing.currency = currency
        existing.variant_title = variant_title or existing.variant_title
        existing.verification = verification
        existing.entered_by = entered_by
        existing.note = note
        existing.purchase_amount = purchase_amount
        existing.extra_amount = extra_amount
        existing.observed_at = now
        row = existing
    else:
        row = models.ProductCost(
            store_id=store_id, channel_id=channel_id,
            external_product_id=product_id, external_variant_id=variant_id or "",
            variant_title=variant_title, amount=amount,
            purchase_amount=purchase_amount, extra_amount=extra_amount,
            currency=currency,
            effective_from=effective_from, source=source, verification=verification,
            entered_by=entered_by, note=note, observed_at=now, created_at=now)
    db.add(row)
    if commit:
        db.commit()
    return row


def history(db: Session, *, store_id, product_id: str | None = None
            ) -> list[models.ProductCost]:
    query = select(models.ProductCost).where(models.ProductCost.store_id == store_id)
    if product_id:
        query = query.where(models.ProductCost.external_product_id == product_id)
    return list(db.scalars(query.order_by(
        models.ProductCost.external_product_id,
        models.ProductCost.external_variant_id,
        models.ProductCost.effective_from.desc())))


def _pick(rows: list[models.ProductCost], *, on: dt.date) -> Cost | None:
    """The value in force on that day: exact variant first, then most recent."""
    usable = [row for row in rows if row.effective_from <= on]
    if not usable:
        return None
    # Order matters and is deliberate. An exact variant beats a product-wide
    # fallback; a later start date beats an earlier one; and on the same date a
    # seller's own figure beats Shopify's, because typing one is a deliberate
    # act and a sync is not. Only then does the newest observation win.
    usable.sort(key=lambda row: (
        1 if row.external_variant_id else 0,
        row.effective_from,
        _SOURCE_RANK.get(row.source, 0),
        row.observed_at,
    ), reverse=True)
    best = usable[0]
    return Cost(amount=Decimal(str(best.amount)), currency=best.currency,
                effective_from=best.effective_from, source=best.source,
                verification=best.verification,
                variant_id=best.external_variant_id, observed_at=best.observed_at)


def cost_on(db: Session, *, store_id, product_id: str, variant_id: str,
            on: dt.date) -> Cost | None:
    """What one unit of this variant cost on that day, or nothing.

    Nothing means unknown. It never means zero — a cost nobody has supplied and
    a cost of nothing are opposite claims, and only one of them can be measured.
    """
    if is_unknown_variant(variant_id):
        return None
    rows = list(db.scalars(select(models.ProductCost).where(
        models.ProductCost.store_id == store_id,
        models.ProductCost.external_product_id == product_id,
        models.ProductCost.external_variant_id.in_([variant_id or "", ""]),
    )))
    return _pick(rows, on=on)


def costs_for_days(db: Session, *, store_id, variants: set[tuple[str, str]],
                   days: list[dt.date]) -> dict[tuple[str, str, dt.date], Cost | None]:
    """Every (product, variant, day) a measurement needs, looked up once each."""
    answers: dict[tuple[str, str, dt.date], Cost | None] = {}
    for product_id, variant_id in variants:
        rows = list(db.scalars(select(models.ProductCost).where(
            models.ProductCost.store_id == store_id,
            models.ProductCost.external_product_id == product_id,
            models.ProductCost.external_variant_id.in_([variant_id or "", ""]),
        ))) if not is_unknown_variant(variant_id) else []
        for day in days:
            answers[(product_id, variant_id, day)] = _pick(rows, on=day)
    return answers
