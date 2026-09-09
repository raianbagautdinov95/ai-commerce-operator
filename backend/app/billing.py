"""Server-owned plans, trial lifecycle and feature entitlements."""
from __future__ import annotations

import datetime as dt
import os
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import models

#: One plan for the pilot. The other two remain in PLANS because existing rows
#: name them and a subscription must not lose its entitlements to a rename, but
#: only this one is sold: a pricing page comparing tiers nobody can buy is a
#: table of fiction.
PILOT_PLAN = "operator"
PILOT_PLAN_NAME = os.getenv("PLAN_NAME", "Operator")


def currency() -> str:
    """Three letters, upper case, from configuration. The price and the currency
    have to travel together or a number means nothing."""
    return (os.getenv("PLAN_CURRENCY", "USD") or "USD").strip().upper()[:3]


#: What the pilot plan actually does today. Every line is a thing that exists —
#: no roadmap, no "coming soon", and nothing about guaranteed returns.
PILOT_FEATURES = (
    "Connects to your Shopify store, read-only",
    "Imports your orders, products and stock every day",
    "Reads them and proposes changes worth making",
    "Never changes anything in your shop without you approving it",
    "Measures what a change was worth, from your own numbers",
    "Withholds profit figures until your costs are complete",
)

PLANS = {
    "starter": {"connected_channels": 1, "monthly_ai_actions": 100, "autopilot": False},
    "operator": {"connected_channels": 3, "monthly_ai_actions": 1000, "autopilot": True},
    "scale": {"connected_channels": 10, "monthly_ai_actions": 10000, "autopilot": True},
}


def ensure_subscription(db: Session, *, store_id: uuid.UUID) -> models.Subscription:
    row = db.scalar(select(models.Subscription).where(models.Subscription.store_id == store_id))
    if row is None:
        days = max(1, min(int(os.getenv("TRIAL_DAYS", "14")), 30))
        row = models.Subscription(
            store_id=store_id, plan="operator", status="trialing",
            trial_ends_at=dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=days),
        )
        db.add(row); db.commit(); db.refresh(row)
    now = dt.datetime.now(dt.timezone.utc)
    trial_end = row.trial_ends_at
    if trial_end.tzinfo is None: trial_end = trial_end.replace(tzinfo=dt.timezone.utc)
    if row.status == "trialing" and trial_end <= now:
        row.status = "trial_expired"; row.updated_at = now
        db.add(row); db.commit(); db.refresh(row)
    return row


def entitlement(plan: str) -> dict:
    return dict(PLANS.get(plan, PLANS["starter"]))


# What the Operator charges per month. The payroll compares its measured impact
# against this, so it must be the real figure — override per deployment.
PLAN_MONTHLY_PRICE = {"starter": 19.0, "operator": 49.0, "scale": 149.0}


def monthly_price(plan: str) -> float:
    override = os.getenv(f"PLAN_PRICE_{plan.upper()}")
    if override:
        try:
            return float(override)
        except ValueError:
            pass
    return PLAN_MONTHLY_PRICE.get(plan, PLAN_MONTHLY_PRICE["starter"])


def cost_for_period(plan: str, days: int) -> float:
    """Pro-rate the subscription across an arbitrary reporting window."""
    return round(monthly_price(plan) * (max(days, 0) / 30.0), 2)
