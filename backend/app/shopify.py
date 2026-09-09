"""Shopify OAuth and webhook trust boundary."""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import os
import re
import secrets
import time
import uuid
from urllib.parse import parse_qsl, urlencode

import httpx

from .runtime import log
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import models

_SHOP_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,58}\.myshopify\.com$")


class ShopifyAuthorizationError(RuntimeError):
    pass


class ShopifyAPIError(RuntimeError):
    pass


class ShopifyFieldUnavailable(ShopifyAPIError):
    """A field this query asked for does not exist here, or is not permitted.

    Separated from any other API error because it has a different answer. A
    shop on an older API version, or one connected before a scope was added,
    must keep syncing with less rather than stop syncing altogether — losing a
    margin figure is a smaller harm than losing the orders.
    """


#: What Shopify says when a field is absent from the schema or barred by scope.
#: Matched on the message as well as the code, because the two Shopify uses for
#: this are not consistently accompanied by an extensions code.
_FIELD_UNAVAILABLE_CODES = {"undefinedfield", "access_denied", "unauthorized_field"}
_FIELD_UNAVAILABLE_PHRASES = (
    "doesn't exist on type",
    "does not exist on type",
    "access denied",
    "requires the following access",
    "not approved to access",
)


def looks_like_a_missing_field(errors: list) -> bool:
    for item in errors or []:
        if not isinstance(item, dict):
            continue
        code = str((item.get("extensions") or {}).get("code") or "").strip().lower()
        message = str(item.get("message") or "").lower()
        if code in _FIELD_UNAVAILABLE_CODES:
            return True
        if any(phrase in message for phrase in _FIELD_UNAVAILABLE_PHRASES):
            return True
    return False


class ShopifyThrottled(RuntimeError):
    """Not a failure: a pause whose length Shopify has told us."""
    def __init__(self, retry_after: float):
        super().__init__(f"Throttled; retry in {retry_after:.2f}s")
        self.retry_after = max(0.0, min(retry_after, THROTTLE_MAX_WAIT_SECONDS))


def normalize_shop(value: str) -> str:
    shop = value.strip().lower().removeprefix("https://").rstrip("/")
    if not _SHOP_RE.fullmatch(shop):
        raise ShopifyAuthorizationError("Invalid Shopify shop domain.")
    return shop


def validate_config() -> None:
    missing = [name for name in ("SHOPIFY_CLIENT_ID", "SHOPIFY_CLIENT_SECRET",
                                  "SHOPIFY_REDIRECT_URI") if not os.getenv(name)]
    if missing:
        raise RuntimeError("Missing Shopify configuration: " + ", ".join(missing))


def _state_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def create_authorization(db: Session, *, store_id: uuid.UUID, actor_id: uuid.UUID,
                         shop: str) -> str:
    validate_config()
    shop = normalize_shop(shop)
    state = secrets.token_urlsafe(32)
    db.add(models.OAuthState(
        state_hash=_state_hash(state), store_id=store_id, actor_id=actor_id,
        provider="shopify", context={"shop": shop},
        expires_at=dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=5),
    ))
    db.commit()
    query = urlencode({
        "client_id": os.environ["SHOPIFY_CLIENT_ID"],
        "scope": os.getenv("SHOPIFY_SCOPES", "read_products,read_orders,read_inventory"),
        "redirect_uri": os.environ["SHOPIFY_REDIRECT_URI"], "state": state,
    })
    return f"https://{shop}/admin/oauth/authorize?{query}"


def verify_callback_query(raw_query: bytes) -> dict[str, str]:
    validate_config()
    pairs = parse_qsl(raw_query.decode("utf-8"), keep_blank_values=True)
    supplied = next((value for key, value in pairs if key == "hmac"), None)
    if not supplied:
        raise ShopifyAuthorizationError("Shopify callback signature is missing.")
    message = urlencode(sorted((key, value) for key, value in pairs
                               if key not in {"hmac", "signature"}))
    expected = hmac.new(os.environ["SHOPIFY_CLIENT_SECRET"].encode(),
                        message.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, supplied):
        raise ShopifyAuthorizationError("Invalid Shopify callback signature.")
    return dict(pairs)


def consume_state(db: Session, state: str, shop: str) -> models.OAuthState:
    row = db.scalar(select(models.OAuthState).where(
        models.OAuthState.state_hash == _state_hash(state),
        models.OAuthState.provider == "shopify",
    ))
    now = dt.datetime.now(dt.timezone.utc)
    expires = row.expires_at if row else None
    if expires is not None and expires.tzinfo is None:
        expires = expires.replace(tzinfo=dt.timezone.utc)
    if row is None or row.used_at is not None or expires <= now or \
            (row.context or {}).get("shop") != shop:
        raise ShopifyAuthorizationError("Invalid or expired Shopify OAuth state.")
    row.used_at = now
    db.add(row); db.commit(); db.refresh(row)
    return row


def exchange_code(shop: str, code: str, *, client: httpx.Client | None = None) -> dict:
    validate_config(); shop = normalize_shop(shop)
    owned = client is None; client = client or httpx.Client(timeout=20)
    try:
        response = client.post(f"https://{shop}/admin/oauth/access_token", json={
            "client_id": os.environ["SHOPIFY_CLIENT_ID"],
            "client_secret": os.environ["SHOPIFY_CLIENT_SECRET"], "code": code,
        })
        if response.status_code >= 400:
            raise ShopifyAuthorizationError(
                f"Shopify token exchange failed ({response.status_code}).")
        payload = response.json()
        if not payload.get("access_token"):
            raise ShopifyAuthorizationError("Shopify did not return an access token.")
        return payload
    finally:
        if owned: client.close()


def verify_webhook(raw_body: bytes, signature: str | None) -> bool:
    validate_config()
    if not signature:
        return False
    expected = base64.b64encode(hmac.new(
        os.environ["SHOPIFY_CLIENT_SECRET"].encode(), raw_body, hashlib.sha256
    ).digest()).decode()
    return hmac.compare_digest(expected, signature)


def credential_provider(shop: str) -> str:
    return "shopify-" + hashlib.sha256(normalize_shop(shop).encode()).hexdigest()[:24]



# Shopify's GraphQL limit is a leaky bucket priced in query cost, not a count of
# requests, and a throttle arrives as HTTP 200 carrying a THROTTLED error. Two
# consequences follow. Treating it as a hard failure fails a whole sync over a
# pause Shopify told us the length of; and retrying the JOB restarts pagination
# from page one, which spends more budget than the attempt that was throttled —
# the retry makes the problem worse. So it is handled here, per request.
THROTTLE_MAX_WAIT_SECONDS = 30.0
THROTTLE_MAX_ATTEMPTS = 4


def is_throttled(errors: list) -> bool:
    return any(isinstance(item, dict)
               and str((item.get("extensions") or {}).get("code", "")).upper() == "THROTTLED"
               for item in errors or [])


def throttle_wait_seconds(extensions: dict | None, *, default: float = 2.0) -> float:
    """How long the bucket needs to afford this query, from Shopify's own numbers.

    Guessing is what turns a pause into an outage: too short and every retry is
    throttled again, too long and a sync stalls for no reason. Shopify reports
    the cost it wanted and the rate the bucket refills at, so the wait is
    arithmetic rather than a guess. Falls back to `default` when it says nothing.
    """
    cost = ((extensions or {}).get("cost") or {})
    status = cost.get("throttleStatus") or {}
    try:
        needed = float(cost.get("requestedQueryCost") or 0)
        available = float(status.get("currentlyAvailable") or 0)
        restore_rate = float(status.get("restoreRate") or 0)
    except (TypeError, ValueError):
        return default
    if restore_rate <= 0:
        return default
    shortfall = needed - available
    if shortfall <= 0:
        return default
    # A little margin: refill is continuous, but our clock and theirs are not.
    return min((shortfall / restore_rate) + 0.25, THROTTLE_MAX_WAIT_SECONDS)


class AdminGraphQLClient:
    """Small read-only Shopify Admin GraphQL client with bounded pagination."""
    def __init__(self, shop: str, access_token: str, *, client: httpx.Client | None = None):
        self.shop = normalize_shop(shop)
        self.access_token = access_token
        self._owned = client is None
        self.client = client or httpx.Client(timeout=30)
        version = os.getenv("SHOPIFY_API_VERSION", "2026-01")
        if not re.fullmatch(r"20\d{2}-(01|04|07|10)", version):
            raise ShopifyAPIError("Invalid Shopify API version configuration.")
        self.url = f"https://{self.shop}/admin/api/{version}/graphql.json"
        #: Fields this shop would not give us, filled in as they are refused.
        #: The caller reads this to decide what it may claim, rather than
        #: guessing from missing values — an absent cost and a refused cost look
        #: identical in the data and mean opposite things.
        self.unavailable: set[str] = set()

    def close(self) -> None:
        if self._owned:
            self.client.close()

    def execute(self, query: str, variables: dict) -> dict:
        """Run one query, waiting out a throttle rather than failing the sync."""
        for attempt in range(THROTTLE_MAX_ATTEMPTS):
            try:
                return self._execute_once(query, variables)
            except ShopifyThrottled as throttled:
                if attempt + 1 >= THROTTLE_MAX_ATTEMPTS:
                    raise ShopifyAPIError(
                        "Shopify kept throttling this request; try a shorter range."
                    ) from throttled
                time.sleep(throttled.retry_after)
        raise ShopifyAPIError("Shopify throttling could not be waited out.")

    def _execute_once(self, query: str, variables: dict) -> dict:
        response = self.client.post(self.url, headers={
            "X-Shopify-Access-Token": self.access_token,
            "Content-Type": "application/json",
        }, json={"query": query, "variables": variables})
        if response.status_code == 429:
            # REST-style throttling, in case a call ever takes that path.
            raise ShopifyThrottled(float(response.headers.get("Retry-After", 2.0)))
        if response.status_code in (401, 403):
            # A revoked token does not un-revoke. Naming this separately is what
            # lets the caller stop retrying and ask the merchant to reconnect,
            # instead of hammering a door that has been locked.
            raise ShopifyAuthorizationError(
                f"Shopify rejected the access token ({response.status_code}); "
                "the app was most likely uninstalled or its access revoked.")
        if response.status_code >= 400:
            raise ShopifyAPIError(f"Shopify Admin API failed ({response.status_code}).")
        payload = response.json()
        if payload.get("errors"):
            if is_throttled(payload["errors"]):
                raise ShopifyThrottled(throttle_wait_seconds(payload.get("extensions")))
            details: list[str] = []
            for item in payload["errors"][:5]:
                if not isinstance(item, dict):
                    continue
                message = str(item.get("message") or "Unknown GraphQL error")[:300]
                code = str((item.get("extensions") or {}).get("code") or "")[:80]
                details.append(f"{code}: {message}" if code else message)
            suffix = "; ".join(details) or "Unknown GraphQL error"
            if looks_like_a_missing_field(payload["errors"]):
                raise ShopifyFieldUnavailable(
                    f"Shopify Admin API refused a field: {suffix}")
            raise ShopifyAPIError(f"Shopify Admin API returned GraphQL errors: {suffix}")
        return payload.get("data") or {}

    #: What the shop kept, per line, rather than what the price list said.
    #:
    #: `discountedTotalSet` is the line after its discounts; `originalTotalSet`
    #: is before them, and using it would credit a restock with money nobody
    #: paid. `taxesIncluded` decides whether the tax in `taxLines` is inside
    #: that figure or beside it. `refunds` is what came back afterwards, and a
    #: refunded unit was not really sold.
    _ORDERS_FULL = """query Orders($cursor: String, $filter: String!) {
      orders(first: 100, after: $cursor, query: $filter, sortKey: CREATED_AT) {
        nodes { id createdAt cancelledAt currencyCode taxesIncluded
          currentTotalPriceSet { shopMoney { amount currencyCode } }
          lineItems(first: 100) { nodes { id quantity
            product { id title }
            variant { id title }
            originalTotalSet { shopMoney { amount } }
            discountedTotalSet { shopMoney { amount currencyCode } }
            taxLines { priceSet { shopMoney { amount } } } } }
          refunds(first: 20) { createdAt
            refundLineItems(first: 100) { nodes { quantity
              lineItem { id }
              subtotalSet { shopMoney { amount } } } } } }
        pageInfo { hasNextPage endCursor }
      }
    }"""

    #: The query that worked before any of that was asked for. A shop on an
    #: older API version keeps its orders; it loses only the margin.
    _ORDERS_LEAN = """query Orders($cursor: String, $filter: String!) {
      orders(first: 100, after: $cursor, query: $filter, sortKey: CREATED_AT) {
        nodes { createdAt cancelledAt currencyCode currentTotalPriceSet {
          shopMoney { amount currencyCode } } lineItems(first: 100) { nodes { quantity
            product { id title }
            originalTotalSet { shopMoney { amount } } } } }
        pageInfo { hasNextPage endCursor }
      }
    }"""

    def order_pages(self, *, created_at_min: dt.datetime, max_pages: int = 100):
        query = self._ORDERS_FULL
        cursor = None
        date_filter = f"created_at:>={created_at_min.astimezone(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}"
        for _ in range(max_pages):
            try:
                data = self.execute(query, {"cursor": cursor, "filter": date_filter})
            except ShopifyFieldUnavailable:
                if query is self._ORDERS_LEAN:
                    raise
                # Drop to what this shop will answer and start the page again.
                # Losing the margin is a smaller harm than losing the orders.
                log.warning("Shopify refused the detailed order fields for %s; "
                            "syncing without variant, discount and refund detail",
                            self.shop)
                self.unavailable.add("order_detail")
                query = self._ORDERS_LEAN
                continue
            connection = data.get("orders") or {}
            yield connection.get("nodes") or []
            page_info = connection.get("pageInfo") or {}
            if not page_info.get("hasNextPage"):
                return
            cursor = page_info.get("endCursor")
            if not cursor:
                raise ShopifyAPIError("Shopify pagination cursor is missing.")
        raise ShopifyAPIError("Shopify order pagination safety limit exceeded.")

    #: `inventoryItem.unitCost` is what the shop paid for one unit, and it is
    #: readable with `read_inventory` — a scope this app already holds, so no
    #: reconnection is asked of anybody. It is often simply not filled in, which
    #: is why the caller must treat a missing value as unknown and never as zero.
    _PRODUCTS_FULL = """query Products($cursor: String) {
      products(first: 100, after: $cursor) {
        nodes { id title status totalInventory tracksInventory isGiftCard
          variants(first: 100) { nodes { id title
            inventoryItem { requiresShipping unitCost { amount currencyCode } } } } }
        pageInfo { hasNextPage endCursor }
      }
    }"""

    _PRODUCTS_LEAN = """query Products($cursor: String) {
      products(first: 100, after: $cursor) {
        nodes { id title status totalInventory tracksInventory isGiftCard
          variants(first: 10) { nodes { inventoryItem { requiresShipping } } } }
        pageInfo { hasNextPage endCursor }
      }
    }"""

    def product_pages(self, *, max_pages: int = 50):
        """Every product with what Shopify says is on the shelf.

        A product Shopify does not count reports `totalInventory` 0, not null —
        which is a trap, because 0 is also what a genuinely empty shelf reports.
        `tracksInventory` is the field that tells them apart, and without it a
        product nobody counts would be proposed for restocking every time it
        sold anything.
        """
        query = self._PRODUCTS_FULL
        cursor = None
        for _ in range(max_pages):
            try:
                data = self.execute(query, {"cursor": cursor})
            except ShopifyFieldUnavailable:
                if query is self._PRODUCTS_LEAN:
                    raise
                log.warning("Shopify refused the variant cost fields for %s; "
                            "syncing stock without cost", self.shop)
                self.unavailable.add("unit_cost")
                query = self._PRODUCTS_LEAN
                continue
            connection = data.get("products") or {}
            yield connection.get("nodes") or []
            page_info = connection.get("pageInfo") or {}
            if not page_info.get("hasNextPage"):
                return
            cursor = page_info.get("endCursor")
            if not cursor:
                raise ShopifyAPIError("Shopify pagination cursor is missing.")
        raise ShopifyAPIError("Shopify product pagination safety limit exceeded.")

    def product_inventory(self, product_id: str) -> int | None:
        """What is on the shelf for one product, read back from the store.

        This is the witness. A restock is only ever called proven because this
        number came from Shopify after the seller said they had done it — not
        because they said so.
        """
        query = """query Product($id: ID!) {
          product(id: $id) { id totalInventory tracksInventory }
        }"""
        product = (self.execute(query, {"id": product_id}) or {}).get("product") or {}
        if not product.get("tracksInventory"):
            return None   # nothing is being counted, so nothing can be witnessed
        value = product.get("totalInventory")
        return None if value is None else int(value)

    def list_webhook_subscriptions(self) -> list[dict]:
        """What Shopify believes it should be calling, and where."""
        query = """{ webhookSubscriptions(first: 50) { edges { node { id topic
          endpoint { ... on WebhookHttpEndpoint { callbackUrl } } } } } }"""
        edges = (self.execute(query, {}).get("webhookSubscriptions") or {}).get("edges") or []
        found = []
        for edge in edges:
            node = edge.get("node") or {}
            found.append({
                "id": node.get("id"),
                "topic": node.get("topic"),
                "uri": (node.get("endpoint") or {}).get("callbackUrl"),
            })
        return found

    def delete_webhook_subscription(self, subscription_id: str) -> None:
        mutation = """mutation Delete($id: ID!) {
          webhookSubscriptionDelete(id: $id) {
            deletedWebhookSubscriptionId
            userErrors { field message }
          }
        }"""
        self.execute(mutation, {"id": subscription_id})

    def ensure_webhook_subscriptions(self, callback_uri: str) -> list[str]:
        """Point every topic at `callback_uri`, removing any aimed somewhere else.

        Development tunnels get a new hostname every restart. Without the cleanup
        below, each restart would leave a subscription aimed at a dead address —
        silently, since Shopify keeps retrying into the void — and the account
        would accumulate them.
        """
        if not callback_uri.startswith("https://"):
            raise ShopifyAPIError("Shopify webhook URI must use HTTPS.")
        topics = ("ORDERS_CREATE", "REFUNDS_CREATE", "APP_UNINSTALLED")

        existing: list[dict] = []
        try:
            existing = self.list_webhook_subscriptions()
        except ShopifyAPIError:
            # Listing is a convenience; failing it must not stop us subscribing.
            pass
        for subscription in existing:
            if subscription.get("topic") in topics and subscription.get("uri") != callback_uri:
                if subscription.get("id"):
                    self.delete_webhook_subscription(subscription["id"])

        mutation = """mutation Subscribe($topic: WebhookSubscriptionTopic!,
          $subscription: WebhookSubscriptionInput!) {
          webhookSubscriptionCreate(topic: $topic, webhookSubscription: $subscription) {
            webhookSubscription { id topic uri }
            userErrors { field message }
          }
        }"""
        registered: list[str] = []
        for topic in topics:
            result = self.execute(mutation, {
                "topic": topic, "subscription": {"uri": callback_uri, "format": "JSON"},
            }).get("webhookSubscriptionCreate") or {}
            errors = result.get("userErrors") or []
            # Re-installation can encounter an existing identical subscription.
            if errors and not all("already" in str(item.get("message", "")).lower()
                                  for item in errors):
                raise ShopifyAPIError("Shopify webhook registration failed.")
            registered.append(topic)
        return registered
