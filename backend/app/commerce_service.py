"""Reading one store's sales and opening what they support proposing.

This lived inside the route until the obvious question was asked: why does a
seller have to press a button to make the Operator look? An operator that waits
to be asked is a tool, and the product does not claim to be one. The work moved
here so the sync can finish and immediately re-read what it just wrote, without
anybody pressing anything.

The route still exists — a seller who has just changed something should not have
to wait for tomorrow — but it is now the second caller rather than the only one.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import actions_engine, commerce_engine
from .db import crud, models
from .deps import _actor_id
from .runtime import log

#: How long a commerce action is given before its result is priced. Two weeks is
#: long enough for a week's noise to average out and short enough that a seller
#: still remembers making the change.
MEASUREMENT_DAYS = 14


def scan_store(db: Session, store, *, days: int = 30) -> dict:
    """Open one action per finding for this store. Returns what it did.

    Commits. Callers that are mid-transaction should be aware of that: the sync
    calls it after its own commit, and the route owns nothing else.
    """
    cutoff = dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=days)

    # Demo channels are excluded rather than merely deprioritised. Advice
    # derived from synthetic sales is advice about nothing, and an action opened
    # from it would sit in the same list as the real ones.
    channels = [row for row in db.scalars(select(models.ChannelConnection).where(
        models.ChannelConnection.store_id == store.id,
        models.ChannelConnection.status == "connected",
    ).order_by(models.ChannelConnection.display_name))
        if not bool((row.settings or {}).get("demo"))
        and not row.external_account_id.startswith("DEMO-")]

    if not channels:
        return {"days": days, "observed_days": 0, "findings": 0,
                "opened": 0, "already_open": 0}

    channel_ids = [channel.id for channel in channels]
    metrics = list(db.scalars(select(models.CommerceDailyMetric).where(
        models.CommerceDailyMetric.store_id == store.id,
        models.CommerceDailyMetric.metric_date >= cutoff,
        models.CommerceDailyMetric.channel_id.in_(channel_ids),
    )))

    series = commerce_engine.daily_series(
        [{"date": row.metric_date, "revenue": float(row.revenue),
          "refunds": float(row.refunds),
          "advertising_spend": float(row.advertising_spend),
          "orders": row.orders, "units": row.units,
          "costs_complete": row.costs_complete} for row in metrics],
        today=dt.datetime.now(dt.timezone.utc).date())

    findings = commerce_engine.analyze(series)

    # The same window, per product. A store-wide total cannot say which product
    # is about to run out, and that is the only commerce finding whose execution
    # can be witnessed rather than taken on trust.
    shelf = list(db.scalars(select(models.ChannelProduct).where(
        models.ChannelProduct.store_id == store.id,
        models.ChannelProduct.channel_id.in_(channel_ids),
    )))
    sold: dict[str, dict] = {}
    for row in db.scalars(select(models.ProductDailyMetric).where(
        models.ProductDailyMetric.store_id == store.id,
        models.ProductDailyMetric.channel_id.in_(channel_ids),
        models.ProductDailyMetric.metric_date >= cutoff,
    )):
        entry = sold.setdefault(row.external_product_id, {"units": 0, "revenue": 0.0})
        entry["units"] += row.units
        entry["revenue"] += float(row.revenue)
    findings.extend(commerce_engine.analyze_products(
        [{"product_id": row.external_product_id, "title": row.title,
          "on_hand": row.on_hand, "status": row.status,
          "is_gift_card": row.is_gift_card, "requires_shipping": row.requires_shipping,
          "units_sold": sold.get(row.external_product_id, {}).get("units", 0),
          "revenue": sold.get(row.external_product_id, {}).get("revenue", 0.0)}
         for row in shelf],
        window_days=len(series),
    ))

    target = channels[0].external_account_id
    currency = channels[0].currency or "USD"
    proposals = actions_engine.proposals_from_commerce(
        [finding.to_dict() for finding in findings], target=target)

    # A scan that runs twice should not double the list in front of anyone.
    # Anything still awaiting a decision counts as already said. The target is
    # part of the key: two products both need restocking, and collapsing them
    # onto the action type alone would silently drop the second one.
    open_keys = {(row.action_type, row.target) for row in db.scalars(
        select(models.OperatorAction).where(
            models.OperatorAction.store_id == store.id,
            models.OperatorAction.module == "commerce",
            models.OperatorAction.status == actions_engine.ActionStatus.PROPOSED.value,
        ))}

    opened = already_open = 0
    for proposal in proposals:
        key = (proposal.action_type, proposal.target)
        if key in open_keys:
            already_open += 1
            continue
        db.add(models.OperatorAction(
            store_id=store.id, module=proposal.module,
            action_type=proposal.action_type, target=proposal.target,
            status=actions_engine.ActionStatus.PROPOSED.value,
            # The finding is measured; the outcome of acting on it is not, and
            # will not be until it has been read back out of the store. Only a
            # measured outcome may ever reach payroll.
            evidence_mode="unverified",
            source_type="commerce_daily_metrics", source_id=target,
            projected_impact=proposal.projected_impact,
            baseline=proposal.baseline, revert_to=proposal.revert_to,
            note=proposal.note, currency=currency,
            measurement_days=MEASUREMENT_DAYS,
        ))
        open_keys.add(key)
        opened += 1

    # The scan itself is evidence even when it correctly opens nothing.  The
    # onboarding checklist must distinguish "ran and found nothing" from
    # "never ran"; only recording scans that opened an action made a healthy
    # young store look permanently stuck on its first analysis.
    crud.append_audit_event(
        db, store_id=store.id, actor_id=_actor_id(store.user_id),
        action="commerce.scan", resource_type="operator_action",
        after={"opened": opened, "already_open": already_open,
               "findings": len(findings), "observed_days": len(series)},
        commit=False,
    )
    db.commit()
    log.info("Commerce scan for %s: %d finding(s), %d opened, %d already open",
             target, len(findings), opened, already_open)
    return {"days": days, "observed_days": len(series), "findings": len(findings),
            "opened": opened, "already_open": already_open}
