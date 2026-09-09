"""WooCommerce read-only application authorization trust boundary."""
from __future__ import annotations

import datetime as dt
import hashlib
import ipaddress
import json
import os
import secrets
import socket
import uuid
from urllib.parse import urlencode, urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session
import httpx

from .db import models


class WooCommerceAuthorizationError(RuntimeError):
    pass


def normalize_store_url(value: str) -> str:
    raw = value.strip()
    if not raw.startswith(("https://", "http://")):
        raw = "https://" + raw
    parsed = urlparse(raw)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise WooCommerceAuthorizationError("WooCommerce store must use a public HTTPS URL.")
    if parsed.port not in (None, 443):
        raise WooCommerceAuthorizationError("Custom WooCommerce ports are not allowed.")
    host = parsed.hostname.lower().rstrip(".")
    if host in {"localhost", "localhost.localdomain"} or "." not in host:
        raise WooCommerceAuthorizationError("WooCommerce store must use a public hostname.")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise WooCommerceAuthorizationError("Private network addresses are not allowed.")
    return f"https://{host}"


def assert_public_dns(site: str) -> None:
    host = urlparse(normalize_store_url(site)).hostname
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)}
    except socket.gaierror as exc:
        raise WooCommerceAuthorizationError("WooCommerce hostname cannot be resolved.") from exc
    if not addresses or any(not ipaddress.ip_address(value).is_global for value in addresses):
        raise WooCommerceAuthorizationError("WooCommerce hostname resolves to a private network.")


def validate_config() -> None:
    missing = [name for name in ("WOOCOMMERCE_CALLBACK_URI", "WOOCOMMERCE_RETURN_URI")
               if not os.getenv(name)]
    if missing:
        raise RuntimeError("Missing WooCommerce configuration: " + ", ".join(missing))
    if not os.environ["WOOCOMMERCE_CALLBACK_URI"].startswith("https://"):
        raise RuntimeError("WOOCOMMERCE_CALLBACK_URI must use HTTPS.")


def _state_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def create_authorization(db: Session, *, store_id: uuid.UUID, actor_id: uuid.UUID,
                         store_url: str) -> str:
    validate_config(); site = normalize_store_url(store_url)
    state = secrets.token_urlsafe(32)
    db.add(models.OAuthState(
        state_hash=_state_hash(state), store_id=store_id, actor_id=actor_id,
        provider="woocommerce", context={"store_url": site},
        expires_at=dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=10),
    ))
    db.commit()
    query = urlencode({
        "app_name": "AI Commerce Operator", "scope": "read", "user_id": state,
        "return_url": os.environ["WOOCOMMERCE_RETURN_URI"],
        "callback_url": os.environ["WOOCOMMERCE_CALLBACK_URI"],
    })
    return f"{site}/wc-auth/v1/authorize?{query}"


def consume_callback(db: Session, payload: dict) -> tuple[models.OAuthState, str, str, str]:
    state = str(payload.get("user_id") or "")
    key = str(payload.get("consumer_key") or "")
    secret = str(payload.get("consumer_secret") or "")
    if not state or not key.startswith("ck_") or not secret.startswith("cs_") or \
            payload.get("key_permissions") != "read":
        raise WooCommerceAuthorizationError("Invalid WooCommerce authorization payload.")
    row = db.scalar(select(models.OAuthState).where(
        models.OAuthState.state_hash == _state_hash(state),
        models.OAuthState.provider == "woocommerce",
    ))
    now = dt.datetime.now(dt.timezone.utc)
    expires = row.expires_at if row else None
    if expires is not None and expires.tzinfo is None:
        expires = expires.replace(tzinfo=dt.timezone.utc)
    if row is None or row.used_at is not None or expires <= now:
        raise WooCommerceAuthorizationError("Invalid or expired WooCommerce state.")
    row.used_at = now; db.add(row); db.commit(); db.refresh(row)
    return row, normalize_store_url((row.context or {}).get("store_url", "")), key, secret


def credential_provider(site: str) -> str:
    return "woocommerce-" + hashlib.sha256(normalize_store_url(site).encode()).hexdigest()[:20]


def serialize_credentials(key: str, secret: str) -> str:
    return json.dumps({"consumer_key": key, "consumer_secret": secret}, separators=(",", ":"))


class WooCommerceAPIError(RuntimeError):
    pass


class ReadOnlyClient:
    def __init__(self, site: str, consumer_key: str, consumer_secret: str,
                 *, client: httpx.Client | None = None):
        self.site = normalize_store_url(site)
        self.key = consumer_key; self.secret = consumer_secret
        self._owned = client is None
        self.client = client or httpx.Client(timeout=30, follow_redirects=False)

    def close(self) -> None:
        if self._owned: self.client.close()

    def order_pages(self, *, after: dt.datetime, max_pages: int = 100):
        # WordPress `_fields` prevents customer identity, billing and shipping data
        # from being returned to the application at all.
        fields = "id,date_created_gmt,total,currency,status,line_items,refunds"
        for page in range(1, max_pages + 1):
            assert_public_dns(self.site)
            response = self.client.get(f"{self.site}/wp-json/wc/v3/orders", auth=(self.key, self.secret),
                params={"after": after.astimezone(dt.timezone.utc).isoformat(), "per_page": 100,
                        "page": page, "orderby": "date", "order": "asc", "_fields": fields})
            if response.status_code == 400 and page > 1:
                return
            if response.status_code >= 400:
                raise WooCommerceAPIError(f"WooCommerce API failed ({response.status_code}).")
            rows = response.json()
            if not isinstance(rows, list):
                raise WooCommerceAPIError("WooCommerce returned an invalid order response.")
            yield rows
            total_pages = int(response.headers.get("X-WP-TotalPages", page))
            if page >= total_pages or len(rows) < 100:
                return
        raise WooCommerceAPIError("WooCommerce pagination safety limit exceeded.")
