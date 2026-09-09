"""What this customer may do, decided in one place.

Before this, nothing asked. A trial that ended and a payment that failed both
left the customer with everything: syncs, analyses, the lot. The subscription
row recorded the truth and no code read it, which is the shape of a product that
is free by accident.

One function answers, and both the screen and the endpoints call it. That is the
point rather than tidiness: a limit enforced only by hiding a button is not a
limit, and a screen that decides for itself will eventually decide differently
from the server that has to mean it.

Six states, and the sixth matters more than it looks:

    trialing        inside the trial window
    active          Stripe says they are paying
    past_due        a payment failed and Stripe is retrying
    canceled        they stopped, or Stripe gave up
    expired         the trial ended and nothing replaced it
    not_configured  this deployment has no Stripe at all

`not_configured` allows everything on purpose. A self-hosted deployment with no
billing must not lock its owner out of their own data because nobody set a
price; refusing there would be inventing a paywall that this deployment never
claimed to have.

What is *never* restricted, in any state: signing in, reading what they already
have, exporting it, asking for deletion, reaching support, and paying. Taking
somebody's data hostage over a failed card is not a business model, and locking
the billing page behind billing is a loop.
"""
from __future__ import annotations

import datetime as dt
import os
from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy.orm import Session

from . import billing

TRIALING = "trialing"
ACTIVE = "active"
PAST_DUE = "past_due"
CANCELED = "canceled"
EXPIRED = "expired"
NOT_CONFIGURED = "not_configured"

#: States in which the paid work may run. Everything else keeps read access and
#: loses only the operations that cost us money to perform.
ALLOWED = frozenset({TRIALING, ACTIVE, NOT_CONFIGURED})

#: What the customer is offered next, by state. `checkout` and `portal` are
#: names, not URLs: the screen asks the API for a link when it needs one, and a
#: client that builds a Stripe URL is a client that can build a wrong one.
_ACTION = {
    TRIALING: "checkout",
    EXPIRED: "checkout",
    CANCELED: "checkout",
    PAST_DUE: "portal",
    ACTIVE: "portal",
    NOT_CONFIGURED: None,
}

_EXPLANATION = {
    TRIALING: "You are on the free trial. Nothing has been charged, and nothing "
              "will be until you choose a plan.",
    ACTIVE: "Your subscription is active.",
    PAST_DUE: "The last payment did not go through. Your data is safe and "
              "nothing has been deleted — updating your card restores the "
              "Operator's work straight away.",
    CANCELED: "Your subscription has ended. Everything you have is still here "
              "and still exportable; the Operator has stopped watching for new "
              "orders.",
    EXPIRED: "Your trial has ended. Nothing has been deleted — everything the "
             "Operator found is still here, and choosing a plan starts it "
             "working again.",
    NOT_CONFIGURED: "This deployment has no billing configured, so nothing is "
                    "charged and nothing is limited.",
}


@dataclass
class Entitlement:
    """The whole contract. Deliberately holds no Stripe or tenant identifier:
    the screen needs none of them, and a value the screen cannot use is a value
    that can only leak."""
    status: str
    access: bool
    #: When the trial or the paid period ends, if either is known. Absent rather
    #: than guessed: a date invented here is a date a customer plans around.
    period_ends_at: dt.datetime | None
    trial_days_remaining: int | None
    action_required: bool
    action: str | None
    explanation: str
    plan_name: str
    price_per_month: float
    currency: str
    #: False when no Stripe price is configured, so the screen can say why
    #: paying is not possible instead of offering a button that 503s.
    checkout_available: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _stripe_configured() -> bool:
    return bool((os.getenv("STRIPE_SECRET_KEY") or "").strip())


def checkout_available() -> bool:
    return _stripe_configured() and bool(
        (os.getenv(f"STRIPE_PRICE_{billing.PILOT_PLAN.upper()}") or "").strip())


def _aware(value: dt.datetime | None) -> dt.datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)


def _status_of(subscription) -> str:
    """Map the stored status onto the contract. `trial_expired` is the stored
    spelling and `expired` the one the rest of the world uses."""
    if not _stripe_configured():
        return NOT_CONFIGURED
    stored = (subscription.status or "").lower()
    return {
        "trialing": TRIALING,
        "active": ACTIVE,
        "past_due": PAST_DUE,
        "unpaid": PAST_DUE,
        "canceled": CANCELED,
        "trial_expired": EXPIRED,
        "expired": EXPIRED,
    }.get(stored, EXPIRED)


def current(db: Session, *, store) -> Entitlement:
    """Read this store's state. Never writes anything except the trial rollover
    that `ensure_subscription` already owns."""
    subscription = billing.ensure_subscription(db, store_id=store.id)
    status = _status_of(subscription)
    now = dt.datetime.now(dt.timezone.utc)

    trial_end = _aware(subscription.trial_ends_at)
    period_end = _aware(subscription.current_period_end)
    ends_at = period_end if status in {ACTIVE, PAST_DUE} else trial_end
    remaining = None
    if status == TRIALING and trial_end is not None:
        seconds = max(0.0, (trial_end - now).total_seconds())
        remaining = int((seconds + 86399) // 86400)

    return Entitlement(
        status=status,
        access=status in ALLOWED,
        period_ends_at=ends_at,
        trial_days_remaining=remaining,
        action_required=status in {PAST_DUE, EXPIRED, CANCELED},
        action=_ACTION.get(status),
        explanation=_EXPLANATION[status],
        plan_name=billing.PILOT_PLAN_NAME,
        price_per_month=billing.monthly_price(billing.PILOT_PLAN),
        currency=billing.currency(),
        checkout_available=checkout_available(),
    )


class PaymentRequired(Exception):
    """Raised where the paid work would have started. Carries the customer's own
    explanation, because a 402 saying "payment required" and nothing else sends
    somebody to support to be told what their own screen could have."""

    def __init__(self, state: Entitlement):
        super().__init__(state.explanation)
        self.state = state


def require_access(db: Session, *, store) -> Entitlement:
    """Gate one paid operation. Call it in the endpoint, not in the template.

    The frontend also hides these buttons, and that is a courtesy rather than a
    control: anybody can call the API directly, and a limit that exists only in
    a browser is not a limit.
    """
    state = current(db, store=store)
    if not state.access:
        raise PaymentRequired(state)
    return state
