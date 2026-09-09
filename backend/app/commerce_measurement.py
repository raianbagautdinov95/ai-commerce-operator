"""When an applied commerce action may be priced, and what the price may claim.

One place, used by both the route a seller can press and the daily job that
needs nobody to press anything. They were never going to stay in step as two
copies, and the difference between them would have been invisible: the same
action measured by hand and by machine has to reach the same number or the
number means nothing.

Three questions, kept apart on purpose, because merging them is how a product
starts claiming money it did not earn:

  Did it happen?      Shopify read the shelf back higher than the proposal was
                      found at. That is `verified_on_hand`, recorded at confirm
                      time, and it is a fact about the world.
  Was it observed?    The window has closed AND a sync has actually read the
                      days inside it. A window that elapsed while nothing was
                      reading Shopify is not a window of zero sales.
  Was it worth it?    Units beyond the old stock could not have been sold
                      without the restock. That is a measurement. Turning those
                      units into profit needs what they cost to buy, which is
                      not in the schema — see PROFIT_IS_PROVABLE.

An action that fails the second question waits. It does not get measured as
zero, and Shopify being unreachable is never read as a bad result.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from decimal import Decimal

from . import actions_engine, product_costs
from .db import models

#: Everything that has to be true before a restock is called proven money.
#: Written down because "it passed" is not an argument, and because each of
#: these has been somebody's mistake somewhere.
REAL_REQUIRES = (
    "Shopify confirmed the stock went up",
    "the window closed and a sync covered every day of it",
    "more units sold than the shelf already held",
    "the revenue of those units is known, after discounts, tax and refunds",
    "the cost of every one of those units is known",
    "the sale and the cost are in the same currency",
    "the arithmetic came from the deterministic engine",
)

# States a seller can be shown. Deliberately more than "measured / not measured":
# "waiting for the window" and "waiting for data" are different situations with
# different answers, and collapsing them tells somebody to be patient when they
# should be looking at a broken sync.
WINDOW_OPEN = "window_open"          # the days are still being lived through
AWAITING_DATA = "awaiting_data"      # the days passed; nothing has read them
AWAITING_COST = "awaiting_cost"      # the sales are known; what they cost is not
READY = "ready"                      # measurable now
MEASURED = "measured"                # done
NOT_MEASURABLE = "not_measurable"    # nothing to measure against, ever


@dataclass
class Window:
    """The days a result is allowed to be read from.

    The day of the change is excluded: it is half before and half after, and
    counting it lets the baseline leak into the result. Today is excluded too —
    it has not finished happening.
    """
    first: dt.date
    last: dt.date
    observed_days: int          # full days inside the window that are over
    complete: bool              # every requested day is over
    days_remaining: int         # full days still to come


def window_for(applied_at: dt.datetime, *, measurement_days: int,
               now: dt.datetime) -> Window:
    applied_on = _as_utc(applied_at).date()
    today = _as_utc(now).date()
    first = applied_on + dt.timedelta(days=1)
    last = first + dt.timedelta(days=measurement_days - 1)
    last_usable = today - dt.timedelta(days=1)
    observed = (min(last_usable, last) - first).days + 1
    # Counted from the last day already banked, but never from before the window
    # opened: on the day of the change itself `last_usable` is still behind
    # `first`, and measuring the gap from there reports fifteen days left of a
    # fortnight.
    banked = max(last_usable, first - dt.timedelta(days=1))
    return Window(
        first=first, last=last,
        observed_days=max(observed, 0),
        complete=last_usable >= last,
        days_remaining=max((last - banked).days, 0),
    )


@dataclass
class Readiness:
    state: str
    reason: str
    window: Window | None = None
    data_state: str = ""


@dataclass
class Outcome:
    """The result of trying to measure one action."""
    measured: bool
    state: str
    reason: str
    impact: dict | None = None
    evidence_mode: str = "unverified"
    detail: dict = field(default_factory=dict)


def _as_utc(moment: dt.datetime) -> dt.datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=dt.timezone.utc)


def shopify_channel(db: Session, store_id) -> models.ChannelConnection | None:
    """Any Shopify connection, not only a healthy one.

    A revoked token does not un-read the days already stored. Refusing to
    measure because the connection later broke would turn somebody's lost
    credential into a lost result.
    """
    return db.scalar(select(models.ChannelConnection).where(
        models.ChannelConnection.store_id == store_id,
        models.ChannelConnection.provider == "shopify",
    ).order_by(models.ChannelConnection.synced_at.desc()))


def coverage(channel: models.ChannelConnection | None,
             window: Window) -> tuple[bool, str]:
    """Has anything actually read the days this result would be read from?

    An elapsed window is not an observed one. If no sync ran, `product_daily_
    metrics` holds nothing for those days — and nothing looks exactly like zero
    sales, which would price a broken sync as a failed restock.
    """
    if channel is None:
        return False, "no Shopify connection to read the days from"
    synced_at = channel.synced_at
    if synced_at is None:
        return False, "this shop has never completed a sync"
    if _as_utc(synced_at).date() <= window.last:
        return False, (f"nothing has read the shop since {window.last.isoformat()}, "
                       f"the last day of the window")
    settings = channel.settings or {}
    synced_from = settings.get("synced_from")
    if not synced_from:
        return False, ("no sync has recorded which days it read, so the window "
                       "cannot be shown to be covered; the next one will")
    try:
        covered_from = dt.date.fromisoformat(str(synced_from))
    except ValueError:
        return False, "the last sync did not record a readable range"
    if covered_from > window.first:
        return False, (f"the last sync only reached back to {covered_from.isoformat()}, "
                       f"and the window opens on {window.first.isoformat()}")
    return True, ""


def readiness(db: Session, row: models.OperatorAction, *,
              now: dt.datetime,
              channel: models.ChannelConnection | None = None) -> Readiness:
    """Where this action stands, in the words a seller should be shown."""
    if row.measured_at is not None:
        return Readiness(MEASURED, "Priced.", None, "complete")
    if row.status != actions_engine.ActionStatus.APPLIED.value:
        return Readiness(NOT_MEASURABLE, "Only a confirmed action is measured.")
    baseline = row.baseline or {}
    if not baseline.get("product_id") or row.applied_at is None:
        return Readiness(NOT_MEASURABLE,
                         "This action has nothing to measure against.")
    if baseline.get("on_hand") is None:
        return Readiness(NOT_MEASURABLE,
                         "No stock level was recorded before the change.")

    window = window_for(row.applied_at, measurement_days=row.measurement_days, now=now)
    if not window.complete:
        return Readiness(
            WINDOW_OPEN,
            (f"{window.days_remaining} full day(s) to go; measuring on "
             f"{(window.last + dt.timedelta(days=1)).isoformat()}."),
            window, "collecting")

    channel = shopify_channel(db, row.store_id) if channel is None else channel
    covered, why = coverage(channel, window)
    if not covered:
        return Readiness(AWAITING_DATA,
                         f"The window closed, but {why}.", window, "incomplete")
    # The days are there. Whether they can be priced is a separate question,
    # and a seller waiting on a missing cost should be told that rather than
    # left reading "ready" on something that is not going to move.
    groups, detail = _sold_units(db, row, window)
    unpriceable = [group for group in groups if group.unit_cost is None]
    if unpriceable:
        names = ", ".join(sorted(
            {(group.variant_title or group.variant_id) for group in unpriceable})[:3])
        return Readiness(
            AWAITING_COST,
            f"The window closed and the sales are known, but no unit cost is on "
            f"record for {names}. Add it and this is priced on the next run.",
            window, "awaiting_cost")
    return Readiness(READY, "The window is closed and the days have been read.",
                     window, "complete")


def _sold_units(db: Session, row: models.OperatorAction,
                window: Window) -> tuple[list[actions_engine.SoldUnits], dict]:
    """What this product sold inside the window, one group per variant per day.

    Per day as well as per variant, because a cost can change mid-window and the
    unit that sold on the 14th must be priced by what it cost on the 14th. A day
    with no row sold nothing — a real observation, once `coverage` has
    established that something read it.
    """
    product_id = str((row.baseline or {}).get("product_id") or "")
    metrics = list(db.scalars(select(models.VariantDailyMetric).where(
        models.VariantDailyMetric.store_id == row.store_id,
        models.VariantDailyMetric.external_product_id == product_id,
        models.VariantDailyMetric.metric_date >= window.first,
        models.VariantDailyMetric.metric_date <= window.last,
    )))

    groups: list[actions_engine.SoldUnits] = []
    without_cost: list[str] = []
    for metric in metrics:
        units = int(metric.units or 0) - int(metric.refunded_units or 0)
        if units <= 0:
            continue        # everything that sold that day came back
        revenue = (Decimal(str(metric.revenue or 0))
                   - Decimal(str(metric.refunded_revenue or 0)))
        cost = product_costs.cost_on(
            db, store_id=row.store_id, product_id=product_id,
            variant_id=metric.external_variant_id, on=metric.metric_date)
        if cost is None:
            without_cost.append(metric.variant_title or metric.external_variant_id)
        groups.append(actions_engine.SoldUnits(
            variant_id=metric.external_variant_id,
            variant_title=metric.variant_title,
            units=units, revenue=revenue,
            unit_cost=None if cost is None else cost.amount,
            currency=metric.currency,
            cost_currency=None if cost is None else cost.currency,
        ))
    detail = {"variants_without_cost": sorted(set(without_cost)),
              "days_with_sales": len({m.metric_date for m in metrics})}
    return groups, detail


def evidence_for(row: models.OperatorAction,
                 impact: actions_engine.RestockImpact) -> tuple[str, str]:
    """May this result be called proven money, and if not, exactly why not.

    Everything measurable has been measured by the time this is asked; what is
    left is whether the claim is safe to make. `REAL_REQUIRES` is the list, and
    the three it can still fail here are the three nothing earlier could rule
    out.
    """
    baseline = row.baseline or {}
    if baseline.get("verified_on_hand") is None:
        return "unverified", ("the change was never read back from Shopify, so it "
                              "is claimed rather than witnessed")
    if impact.provisional:
        return "unverified", "the measurement window has not closed"
    if impact.attributable_units < actions_engine.MIN_ATTRIBUTABLE_UNITS:
        return "unverified", ("nothing sold beyond the stock that was already on "
                              "the shelf, so the restock has not yet been the "
                              "reason for a sale")
    return "real", ""


def measure(db: Session, row: models.OperatorAction, *, now: dt.datetime,
            channel: models.ChannelConnection | None = None,
            commit: bool = True) -> Outcome:
    """Price one applied action, once.

    Writing is guarded by `measured_at`: a second caller — a retry, a second
    scheduler, a seller pressing the button while the job runs — finds the row
    already priced and changes nothing.
    """
    if row.measured_at is not None:
        return Outcome(False, MEASURED, "Already priced.", row.impact,
                       row.evidence_mode)

    state = readiness(db, row, now=now, channel=channel)
    if state.state != READY:
        return Outcome(False, state.state, state.reason)

    window = state.window
    groups, detail = _sold_units(db, row, window)
    result = actions_engine.measure_restock(
        row.baseline, groups, days=window.observed_days,
        requested_days=row.measurement_days)
    if result.impact is None:
        # Not a failure and not a zero. The sales are known; something needed to
        # price them is not, and saying which is the difference between a
        # seller fixing it and a seller waiting for ever.
        state_for = (AWAITING_COST if result.blocked in
                     (actions_engine.COST_UNKNOWN, actions_engine.CURRENCY_MISMATCH)
                     else NOT_MEASURABLE)
        return Outcome(False, state_for, result.reason, detail=detail)

    impact = result.impact
    evidence, why = evidence_for(row, impact)
    payload = impact.to_dict()
    payload["evidence_reason"] = why
    outcome = {"days": impact.days, "units": impact.observed_units,
               "revenue": float(impact.observed_revenue), "spend": 0.0,
               "currency": impact.currency,
               "variants": len({group.variant_id for group in groups})}
    row.outcome = outcome
    row.impact = payload
    row.status = actions_engine.ActionStatus.MEASURED.value
    row.measured_at = _as_utc(now)
    row.evidence_mode = evidence
    db.add(row)
    if commit:
        db.commit()
    return Outcome(True, MEASURED,
                   why or "Measured, witnessed and attributable.",
                   payload, evidence,
                   {**detail, "window_first": window.first.isoformat(),
                    "window_last": window.last.isoformat()})


def due_actions(db: Session, *, store_id, now: dt.datetime,
                limit: int = 100) -> list[models.OperatorAction]:
    """Applied, unpriced, and past the last day of their window.

    The date arithmetic is done here rather than in SQL so that one definition
    of a window serves every caller — a second one written in a WHERE clause is
    a second definition, and it would drift.
    """
    rows = db.scalars(select(models.OperatorAction).where(
        models.OperatorAction.store_id == store_id,
        models.OperatorAction.status == actions_engine.ActionStatus.APPLIED.value,
        models.OperatorAction.measured_at.is_(None),
        models.OperatorAction.applied_at.is_not(None),
    ).order_by(models.OperatorAction.applied_at).limit(limit))
    due = []
    for row in rows:
        window = window_for(row.applied_at, measurement_days=row.measurement_days,
                            now=now)
        if window.complete:
            due.append(row)
    return due
