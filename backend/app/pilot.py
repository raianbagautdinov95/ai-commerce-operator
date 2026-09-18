"""Small, explicit gate for the Shopify feedback pilot.

The offer is for the first ten *connected* Shopify shops.  Signing in, opening
the app, or starting OAuth does not consume a place: only a shop that has
actually completed Shopify's approval does.  This keeps the public promise
honest while the pilot is intentionally small.
"""
from __future__ import annotations

import os

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .db import models


def maximum_stores() -> int:
    """A bounded operator setting, never an unbounded public claim."""
    try:
        value = int(os.getenv("PILOT_MAX_SHOPIFY_STORES", "10"))
    except ValueError:
        value = 10
    return max(1, min(value, 100))


def enrolled_shopify_stores(db: Session) -> int:
    """Count only shops that actually completed a live Shopify connection."""
    return db.scalar(select(func.count()).select_from(models.ChannelConnection).where(
        models.ChannelConnection.provider == "shopify",
        models.ChannelConnection.status == "connected",
        models.ChannelConnection.external_account_id.like("%.myshopify.com"),
    )) or 0


def has_space_for_shopify(db: Session, *, shop: str) -> bool:
    """Return true for an existing pilot shop, or while a place remains."""
    # A store that completed OAuth before is an enrolled participant even if it
    # later disconnects. It must be able to reconnect after the cohort fills.
    previous_connection = db.scalar(
        select(models.ChannelConnection.id).where(
            models.ChannelConnection.provider == "shopify",
            models.ChannelConnection.external_account_id == shop,
        ).limit(1)
    )
    if previous_connection:
        return True

    return enrolled_shopify_stores(db) < maximum_stores()
