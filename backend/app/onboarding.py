"""Where a new customer actually is, asked of the database rather than the browser.

An onboarding checklist is a promise about the world, and the easy way to build
one is to tick a step when somebody presses the button that starts it. That
produces a screen saying "Shopify connected" over a store whose token was never
stored, and "synced" over a job that was queued and then failed — which is worse
than no checklist, because the customer stops looking for the thing that is
wrong.

So every status here is derived from something that would still be true if the
browser were closed: a credential that decrypts, a delivery that verified, a
metric row with a date on it. Nothing is passed in from the client, and there is
no "mark as done".

Four states, and the fourth is the one that earns its keep:

    not_started       nothing has happened yet
    in_progress       started, and not finished
    complete          the evidence for it exists
    needs_attention   it looked finished and something is now wrong

`needs_attention` exists because "connected" and "working" are different claims.
A shop with live subscriptions whose deliveries are being refused is connected
and broken at the same time, and a checklist that cannot say both will say the
flattering one.
"""
from __future__ import annotations

import datetime as dt
import os
from dataclasses import asdict, dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import credentials, shopify, webhook_health
from .db import models

NOT_STARTED = "not_started"
IN_PROGRESS = "in_progress"
COMPLETE = "complete"
NEEDS_ATTENTION = "needs_attention"

#: How long after a sync we still expect an analysis to have run. The sync
#: triggers it directly, so a gap wider than this means the follow-up failed.
ANALYSIS_GRACE_MINUTES = 10


@dataclass
class Step:
    key: str
    title: str
    status: str
    detail: str
    #: The one safe thing a person can do about it, or None. Every action named
    #: here is idempotent: pressing it twice costs a round trip and nothing else.
    action: str | None = None
    action_label: str | None = None
    #: Where to read more, for the states a sentence cannot fix.
    help_url: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Onboarding:
    steps: list[Step] = field(default_factory=list)
    support_email: str | None = None
    support_response_time: str | None = None
    support_url: str | None = None

    @property
    def complete(self) -> bool:
        return all(step.status == COMPLETE for step in self.steps)

    def to_dict(self) -> dict[str, Any]:
        return {"steps": [s.to_dict() for s in self.steps],
                "complete": self.complete,
                "support_email": self.support_email,
                "support_response_time": self.support_response_time,
                "support_url": self.support_url}


def support() -> dict[str, str | None]:
    """Read from the environment, never hard-coded: an address in the source is
    one that cannot be changed without a deploy, and it is usually somebody's
    personal mail."""
    return {
        "support_email": (os.getenv("SUPPORT_EMAIL") or "").strip() or None,
        "support_response_time": (os.getenv("SUPPORT_RESPONSE_TIME") or "").strip() or None,
        "support_url": (os.getenv("SUPPORT_URL") or "").strip() or None,
    }


def _channel(db: Session, store_id) -> models.ChannelConnection | None:
    return db.scalar(select(models.ChannelConnection).where(
        models.ChannelConnection.store_id == store_id,
        models.ChannelConnection.provider == "shopify",
    ).order_by(models.ChannelConnection.connected_at.desc()))


def _account_step(store) -> Step:
    return Step("account", "Your account", COMPLETE,
                "Signed in. Your data is yours alone — every other customer's is "
                "invisible to it, and the database enforces that, not the app.")


def _permissions_step(channel) -> Step:
    """Said before the connection, because it is what the customer is agreeing
    to. Read-only is a fact about the scopes, not a reassurance."""
    scopes = ", ".join((channel.settings or {}).get("scopes") or []) if channel else ""
    granted = f" You granted: {scopes}." if scopes else ""
    return Step(
        "permissions", "What the Operator may do", COMPLETE,
        "Read-only. It can see products, orders and stock, and it holds no "
        "permission to change anything in your shop — not a price, not a "
        "product, not an order." + granted)


def _oauth_step(db: Session, store_id, channel) -> Step:
    if channel is None:
        return Step("oauth", "Connect Shopify", NOT_STARTED,
                    "Not connected yet.",
                    action="connect_shopify", action_label="CONNECT SHOPIFY")
    if channel.status != "connected":
        reason = (channel.settings or {}).get("revoked_reason")
        return Step("oauth", "Connect Shopify", NEEDS_ATTENTION,
                    reason or "The connection was ended.",
                    action="connect_shopify", action_label="RECONNECT SHOPIFY")
    return Step("oauth", "Connect Shopify", COMPLETE,
                f"Connected to {channel.external_account_id}.")


def _token_step(db: Session, store_id, channel) -> Step:
    """A channel row is not a working connection. This asks whether the token
    is there and decrypts, which is the only version of the question that
    matters when a sync is about to need it."""
    if channel is None or channel.status != "connected":
        return Step("token", "Access token", NOT_STARTED, "Waiting for a connection.")
    try:
        token = credentials.load_credential(
            db, store_id=store_id,
            provider=shopify.credential_provider(channel.external_account_id))
    except Exception:  # noqa: BLE001 - a broken keyring must read as attention
        token = None
    if not token:
        return Step("token", "Access token", NEEDS_ATTENTION,
                    "The shop is linked but its access token is missing or cannot "
                    "be read. Reconnecting issues a new one.",
                    action="connect_shopify", action_label="RECONNECT SHOPIFY")
    return Step("token", "Access token", COMPLETE, "Stored and readable.")


def _notifications_step(channel) -> Step:
    if channel is None or channel.status != "connected":
        return Step("notifications", "Order notifications", NOT_STARTED,
                    "Waiting for a connection.")
    settings = channel.settings or {}
    topics = settings.get("webhooks") or []
    configured = os.getenv("SHOPIFY_WEBHOOK_URI")
    if webhook_health.signatures_failing():
        return Step(
            "notifications", "Order notifications", NEEDS_ATTENTION,
            "Shopify is sending order notifications and they are being refused: "
            "their signature does not match the key this server holds. Orders "
            "will stop arriving until that is fixed.",
            action="open_help", action_label="HOW TO FIX THIS",
            help_url="/help/failing-signature")
    if not topics:
        return Step("notifications", "Order notifications", NEEDS_ATTENTION,
                    settings.get("webhook_error")
                    or "Shopify has not accepted the subscriptions yet.",
                    action="retry_notifications", action_label="TRY AGAIN")
    registered = settings.get("webhook_uri")
    if configured and registered and registered != configured:
        return Step("notifications", "Order notifications", NEEDS_ATTENTION,
                    "The notifications point at an address this server no longer "
                    "answers, so orders are arriving nowhere.",
                    action="retry_notifications", action_label="POINT THEM HERE")
    return Step("notifications", "Order notifications", COMPLETE,
                f"{len(topics)} subscribed.")


def _delivery_step(db: Session, store_id, channel) -> Step:
    """Separate from the subscription: one says Shopify agreed to tell us, the
    other says it has and we understood."""
    if channel is None or channel.status != "connected":
        return Step("delivery", "First order notification", NOT_STARTED,
                    "Waiting for a connection.")
    latest = db.scalar(select(models.WebhookDelivery).where(
        models.WebhookDelivery.store_id == store_id,
        models.WebhookDelivery.provider == "shopify",
        models.WebhookDelivery.status == "completed",
    ).order_by(models.WebhookDelivery.received_at.desc()))
    if latest is None:
        return Step("delivery", "First order notification", IN_PROGRESS,
                    "None yet. One arrives with your next order — nothing to do "
                    "but wait, or place a test order.")
    return Step("delivery", "First order notification", COMPLETE,
                f"Last received {latest.received_at:%Y-%m-%d %H:%M} UTC.")


#: When a daily sync stops being a daily sync. Two days lets one run fail and be
#: retried without telling a seller something is wrong when it is not.
SYNC_STALE_HOURS = int(os.getenv("ONBOARDING_SYNC_STALE_HOURS", "48"))


def _sync_step(db: Session, store_id, channel) -> Step:
    if channel is None or channel.status != "connected":
        return Step("sync", "First sync", NOT_STARTED, "Waiting for a connection.")
    metrics = db.scalar(select(models.CommerceDailyMetric).where(
        models.CommerceDailyMetric.store_id == store_id,
    ).order_by(models.CommerceDailyMetric.synced_at.desc()))
    running = db.scalar(select(models.IdempotencyRecord).where(
        models.IdempotencyRecord.store_id == store_id,
        models.IdempotencyRecord.status == "processing",
    ))
    if running is not None and str(running.operation or "").startswith("shopify.sync"):
        return Step("sync", "First sync", IN_PROGRESS,
                    "Running now. This reads your last 30 days of orders.")
    if metrics is None:
        failed = db.scalar(select(models.IdempotencyRecord).where(
            models.IdempotencyRecord.store_id == store_id,
            models.IdempotencyRecord.status == "failed",
        ))
        if failed is not None:
            return Step("sync", "First sync", NEEDS_ATTENTION,
                        "The last sync did not finish. Running it again is safe: "
                        "it overwrites by day rather than adding.",
                        action="run_sync", action_label="TRY AGAIN")
        return Step("sync", "First sync", NOT_STARTED,
                    "Not run yet. It reads your last 30 days of orders and changes "
                    "nothing in your shop.",
                    action="run_sync", action_label="RUN THE FIRST SYNC")
    # A daily sync runs by itself now, so a timestamp days old is not a
    # completed step — it is a sync that stopped. Reporting it as done is how a
    # seller ends up reading last week's numbers as this week's.
    last = metrics.synced_at
    if last is not None and last.tzinfo is None:
        last = last.replace(tzinfo=dt.timezone.utc)
    age_hours = ((dt.datetime.now(dt.timezone.utc) - last).total_seconds() / 3600
                 if last else None)
    if age_hours is not None and age_hours > SYNC_STALE_HOURS:
        return Step("sync", "Daily sync", NEEDS_ATTENTION,
                    f"Last read {int(age_hours)} hours ago. It is meant to run "
                    f"every day, so your figures are behind and any result "
                    f"waiting on those days cannot be measured.",
                    action="run_sync", action_label="RUN IT NOW")
    return Step("sync", "First sync", COMPLETE,
                f"Last synced {metrics.synced_at:%Y-%m-%d %H:%M} UTC.")


def _analysis_step(db: Session, store_id, sync_step: Step) -> Step:
    """The step that used to be invisible. A sync that worked and an analysis
    that never ran look identical from the outside, and the second is the one
    that decides whether the Proposals screen has anything on it."""
    if sync_step.status != COMPLETE:
        return Step("analysis", "First analysis", NOT_STARTED,
                    "Runs by itself once your orders are in.")
    proposals = list(db.scalars(select(models.OperatorAction).where(
        models.OperatorAction.store_id == store_id,
        models.OperatorAction.module == "commerce",
    )))
    if proposals:
        waiting = [p for p in proposals if p.status == "proposed"]
        if waiting:
            return Step("analysis", "First analysis", COMPLETE,
                        f"{len(waiting)} proposal(s) waiting for you.",
                        action="open_proposals", action_label="SEE THEM")
        return Step("analysis", "First analysis", COMPLETE,
                    "Ran, and you have decided on everything it found.")
    events = db.scalar(select(models.AuditEvent).where(
        models.AuditEvent.store_id == store_id,
        models.AuditEvent.action == "commerce.scan",
    ).order_by(models.AuditEvent.created_at.desc()))
    if events is not None:
        return Step("analysis", "First analysis", COMPLETE,
                    "Ran and found nothing worth proposing. That is an answer, not "
                    "a failure: a week of history is the minimum, and a shop with "
                    "nothing wrong produces no proposals.",
                    action="run_analysis", action_label="READ MY SALES AGAIN")
    return Step("analysis", "First analysis", NEEDS_ATTENTION,
                "Your orders are in, but nothing has read them yet. Running the "
                "analysis again is safe — it opens nothing twice.",
                action="run_analysis", action_label="READ MY SALES NOW")


def status(db: Session, *, store) -> Onboarding:
    """Every step, judged on evidence. Reads only this tenant's rows."""
    channel = _channel(db, store.id)
    sync = _sync_step(db, store.id, channel)
    steps = [
        _account_step(store),
        _permissions_step(channel),
        _oauth_step(db, store.id, channel),
        _token_step(db, store.id, channel),
        _notifications_step(channel),
        _delivery_step(db, store.id, channel),
        sync,
        _analysis_step(db, store.id, sync),
    ]
    return Onboarding(steps=steps, **support())
