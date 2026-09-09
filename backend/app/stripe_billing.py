"""Minimal Stripe Billing boundary: hosted Checkout, Portal and signed events."""
from __future__ import annotations
import hashlib, hmac, os, time
import httpx

class StripeConfigurationError(RuntimeError): pass
class StripeAPIError(RuntimeError): pass

def _config(name: str) -> str:
    value = os.getenv(name)
    if not value: raise StripeConfigurationError(f"Missing Stripe configuration: {name}")
    return value

def api_version() -> str | None:
    """The Stripe API version to ask for, if this deployment pins one.

    Unpinned, every request is answered in whatever version the *account* is
    currently on, and Stripe moves that on its own schedule. The shape of a
    subscription is not a detail here — it is how this product decides who may
    use it — so an integration that reads one shape and is served another fails
    silently: no error, just a field that stops arriving.

    Pinning is the owner's call because the right value is whichever version
    their account and their webhook endpoints already speak. `_period_end`
    below reads both shapes regardless, so an unpinned deployment still works;
    pinning removes the surprise rather than the correctness.
    """
    return (os.getenv("STRIPE_API_VERSION") or "").strip() or None


def _headers() -> dict[str, str]:
    version = api_version()
    return {"Stripe-Version": version} if version else {}


def _post(path: str, data: dict, *, client: httpx.Client | None = None) -> dict:
    owned = client is None; client = client or httpx.Client(timeout=20)
    try:
        response = client.post(f"https://api.stripe.com/v1/{path}", auth=(_config("STRIPE_SECRET_KEY"), ""),
                               data=data, headers=_headers())
        if response.status_code >= 400: raise StripeAPIError(f"Stripe API failed ({response.status_code}).")
        return response.json()
    finally:
        if owned: client.close()


def period_end(subscription: dict) -> int | None:
    """When the paid period runs out, from wherever this payload carries it.

    Stripe reports this on the subscription in some API versions and on the
    subscription's items in others. Reading only the first place is what made
    the failure invisible: the field simply stops arriving, nothing raises, and
    the date a customer is shown quietly stops moving.

    Returns None when neither is present, which the caller must treat as "not
    told" rather than as an expiry.
    """
    value = subscription.get("current_period_end")
    if value is None:
        items = ((subscription.get("items") or {}).get("data") or [])
        ends = [item.get("current_period_end") for item in items
                if isinstance(item, dict) and item.get("current_period_end") is not None]
        # The furthest out: a subscription with several items ends when its last
        # item does, and choosing the earliest would cut somebody's access short.
        value = max(ends) if ends else None
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


#: Checkout completes for a subscription even when the money has not arrived —
#: a card that needs authentication, or one that failed after the session was
#: submitted. Only these two mean it is safe to hand over the product.
PAID_STATUSES = {"paid", "no_payment_required"}


def checkout_is_paid(session: dict) -> bool:
    """Did this Checkout session actually take the money?

    A session that completed unpaid leaves the subscription `incomplete`, and
    Stripe will send `customer.subscription.updated` if and when it settles.
    Activating on the session alone gives the product away to somebody whose
    payment never landed.

    A payload with no `payment_status` at all is treated as paid: the field has
    been on the session for years, and refusing every activation because a key
    is unexpectedly absent would lock out paying customers to guard against a
    case that has not happened. What must never be ignored is the field saying
    plainly that it was not paid.
    """
    status = session.get("payment_status")
    return status is None or str(status) in PAID_STATUSES

def create_checkout(*, store_id: str, email: str, plan: str, customer: str | None = None,
                    client: httpx.Client | None = None) -> str:
    if plan not in {"starter", "operator", "scale"}: raise ValueError("Unknown billing plan.")
    data = {"mode": "subscription", "line_items[0][price]": _config(f"STRIPE_PRICE_{plan.upper()}"),
            "line_items[0][quantity]": "1", "success_url": _config("STRIPE_SUCCESS_URL"),
            "cancel_url": _config("STRIPE_CANCEL_URL"), "client_reference_id": store_id,
            "metadata[store_id]": store_id, "metadata[plan]": plan,
            "subscription_data[metadata][store_id]": store_id,
            "subscription_data[metadata][plan]": plan}
    data["customer" if customer else "customer_email"] = customer or email
    payload = _post("checkout/sessions", data, client=client)
    if not payload.get("url"): raise StripeAPIError("Stripe did not return a Checkout URL.")
    return payload["url"]

def create_portal(*, customer: str, client: httpx.Client | None = None) -> str:
    payload = _post("billing_portal/sessions", {"customer": customer,
                    "return_url": _config("STRIPE_PORTAL_RETURN_URL")}, client=client)
    if not payload.get("url"): raise StripeAPIError("Stripe did not return a Portal URL.")
    return payload["url"]

def verify_event(body: bytes, signature: str | None, *, now: int | None = None) -> None:
    parts = {}
    for item in (signature or "").split(","):
        if "=" in item:
            key, value = item.split("=", 1); parts.setdefault(key, []).append(value)
    try: timestamp = int(parts["t"][0])
    except (KeyError, ValueError): raise ValueError("Invalid Stripe signature header.")
    if abs((now or int(time.time())) - timestamp) > 300: raise ValueError("Expired Stripe webhook signature.")
    expected = hmac.new(_config("STRIPE_WEBHOOK_SECRET").encode(),
                        str(timestamp).encode() + b"." + body, hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(expected, value) for value in parts.get("v1", [])):
        raise ValueError("Invalid Stripe webhook signature.")
