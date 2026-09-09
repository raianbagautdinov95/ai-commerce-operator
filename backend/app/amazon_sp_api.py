"""Modern Amazon SP-API OAuth and read-only HTTP client (no legacy SigV4)."""
from __future__ import annotations

import datetime as dt
import gzip
import hashlib
import os
import secrets
import time
import threading
import uuid
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import credentials
from .db import models

LWA_TOKEN_URL = "https://api.amazon.com/auth/o2/token"
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
REGIONAL_ENDPOINTS = {
    "NA": "https://sellingpartnerapi-na.amazon.com",
    "EU": "https://sellingpartnerapi-eu.amazon.com",
    "FE": "https://sellingpartnerapi-fe.amazon.com",
}
_ACCESS_TOKEN_CACHE: dict[str, tuple[str, float]] = {}
_TOKEN_LOCK = threading.Lock()


class AmazonAuthorizationError(RuntimeError):
    pass


class AmazonAPIError(RuntimeError):
    pass


MAX_REPORT_DOCUMENT_BYTES = 20 * 1024 * 1024


def validate_sp_api_config() -> None:
    required = ("SP_API_APPLICATION_ID", "SP_API_CLIENT_ID", "SP_API_CLIENT_SECRET",
                "SP_API_REDIRECT_URI", "SP_API_AUTHORIZATION_URL")
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise RuntimeError("Missing Amazon SP-API configuration: " + ", ".join(missing))


def _state_hash(state: str) -> str:
    return hashlib.sha256(state.encode()).hexdigest()


def create_authorization(db: Session, *, store_id: uuid.UUID, actor_id: uuid.UUID) -> str:
    validate_sp_api_config()
    state = secrets.token_urlsafe(32)
    db.add(models.OAuthState(
        state_hash=_state_hash(state), store_id=store_id, actor_id=actor_id,
        provider="amazon-sp-api",
        expires_at=dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=5),
    ))
    db.commit()
    query = {
        "application_id": os.environ["SP_API_APPLICATION_ID"],
        "state": state,
        "redirect_uri": os.environ["SP_API_REDIRECT_URI"],
    }
    if os.getenv("SP_API_APP_VERSION") == "beta":
        query["version"] = "beta"
    return os.environ["SP_API_AUTHORIZATION_URL"] + "?" + urlencode(query)


def consume_authorization_state(db: Session, state: str) -> models.OAuthState:
    row = db.scalar(select(models.OAuthState).where(
        models.OAuthState.state_hash == _state_hash(state),
        models.OAuthState.provider == "amazon-sp-api",
    ))
    now = dt.datetime.now(dt.timezone.utc)
    expires_at = row.expires_at if row is not None else None
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=dt.timezone.utc)
    if row is None or row.used_at is not None or expires_at <= now:
        raise AmazonAuthorizationError("Invalid or expired OAuth state.")
    row.used_at = now
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _token_request(data: dict, *, client: httpx.Client | None = None) -> dict:
    validate_sp_api_config()
    owned = client is None
    client = client or httpx.Client(timeout=20.0)
    try:
        response = client.post(LWA_TOKEN_URL, data={
            **data,
            "client_id": os.environ["SP_API_CLIENT_ID"],
            "client_secret": os.environ["SP_API_CLIENT_SECRET"],
        })
        if response.status_code >= 400:
            raise AmazonAuthorizationError(f"Amazon LWA token exchange failed ({response.status_code}).")
        payload = response.json()
        if not payload.get("access_token"):
            raise AmazonAuthorizationError("Amazon LWA response did not contain an access token.")
        return payload
    finally:
        if owned:
            client.close()


def exchange_authorization_code(code: str, *, client: httpx.Client | None = None) -> dict:
    return _token_request({
        "grant_type": "authorization_code", "code": code,
        "redirect_uri": os.environ["SP_API_REDIRECT_URI"],
    }, client=client)


def refresh_access_token(refresh_token: str, *, client: httpx.Client | None = None) -> str:
    return _token_request({"grant_type": "refresh_token", "refresh_token": refresh_token},
                          client=client)["access_token"]


def _token_cache_key(store_id: uuid.UUID, refresh_token: str) -> str:
    """Bind the cache entry to the credential, not just the store.

    Keying on store_id alone would keep serving the previous account's access
    token (for up to its full lifetime) after a store reconnects to a different
    Amazon account. Hashing the refresh token makes a rotated credential miss.
    """
    digest = hashlib.sha256(refresh_token.encode()).hexdigest()[:16]
    return f"{store_id}:{digest}"


def _access_token_for_store(store_id: uuid.UUID, refresh_token: str,
                            client: httpx.Client | None = None) -> str:
    cache_key = _token_cache_key(store_id, refresh_token)
    with _TOKEN_LOCK:
        now = time.monotonic()
        cached = _ACCESS_TOKEN_CACHE.get(cache_key)
        if cached and cached[1] > now + 60:
            return cached[0]
        payload = _token_request(
            {"grant_type": "refresh_token", "refresh_token": refresh_token}, client=client
        )
        expires_in = max(int(payload.get("expires_in", 3600)), 120)
        # Drop entries superseded by credential rotation so the map stays bounded.
        for stale in [k for k, (_, exp) in _ACCESS_TOKEN_CACHE.items() if exp <= now]:
            del _ACCESS_TOKEN_CACHE[stale]
        _ACCESS_TOKEN_CACHE[cache_key] = (payload["access_token"], now + expires_in)
        return payload["access_token"]


@dataclass
class SPAPIClient:
    access_token: str
    region: str = "EU"
    client: httpx.Client | None = None
    max_attempts: int = 4

    def __post_init__(self) -> None:
        # request() reads `response` after the retry loop, so at least one pass
        # must always run.
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1.")

    def request(self, method: str, path: str, *, params: dict | None = None,
                json: dict | None = None, retry_safe: bool = False) -> dict:
        endpoint = REGIONAL_ENDPOINTS.get(self.region.upper())
        if endpoint is None:
            raise ValueError("Unsupported SP-API region.")
        owned = self.client is None
        client = self.client or httpx.Client(timeout=30.0)
        try:
            for attempt in range(self.max_attempts):
                response = client.request(
                    method, endpoint + path, params=params, json=json,
                    headers={
                        "x-amz-access-token": self.access_token,
                        "x-amz-date": dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
                        "user-agent": os.getenv("SP_API_USER_AGENT", "AICommerceOperator/0.1"),
                    },
                )
                may_retry = method.upper() in {"GET", "HEAD"} or retry_safe
                if response.status_code not in RETRYABLE_STATUS or not may_retry:
                    break
                if attempt + 1 < self.max_attempts:
                    delay = min(float(response.headers.get("Retry-After", 2 ** attempt)), 30.0)
                    time.sleep(max(delay, 0.0))
            if response.status_code >= 400:
                request_id = response.headers.get("x-amzn-RequestId", "unknown")
                raise AmazonAPIError(
                    f"Amazon SP-API request failed ({response.status_code}, request_id={request_id})."
                )
            return response.json()
        finally:
            if owned:
                client.close()

    def marketplace_participations(self) -> dict:
        return self.request("GET", "/sellers/v1/marketplaceParticipations")

    def listing_pages(self, *, seller_id: str, marketplace_id: str):
        path = f"/listings/2021-08-01/items/{seller_id}"
        yield from self.paginate(
            path,
            params={"marketplaceIds": marketplace_id, "pageSize": 20,
                    "includedData": "summaries,issues,offers,fulfillmentAvailability"},
            token_param="pageToken",
        )

    def inventory_pages(self, *, marketplace_id: str):
        yield from self.paginate(
            "/fba/inventory/v1/summaries",
            params={"details": "true", "granularityType": "Marketplace",
                    "granularityId": marketplace_id, "marketplaceIds": marketplace_id},
            token_param="nextToken",
        )

    def create_sales_traffic_report(self, *, marketplace_id: str,
                                    start: dt.datetime, end: dt.datetime) -> dict:
        return self.request(
            "POST", "/reports/2021-06-30/reports",
            json={
                "reportType": "GET_SALES_AND_TRAFFIC_REPORT",
                "marketplaceIds": [marketplace_id],
                "dataStartTime": start.isoformat(),
                "dataEndTime": end.isoformat(),
                "reportOptions": {"dateGranularity": "DAY", "asinGranularity": "SKU"},
            },
        )

    def get_report(self, report_id: str) -> dict:
        return self.request("GET", f"/reports/2021-06-30/reports/{report_id}")

    def get_report_document(self, document_id: str) -> dict:
        return self.request("GET", f"/reports/2021-06-30/documents/{document_id}")

    def transaction_pages(self, *, marketplace_id: str, posted_after: dt.datetime,
                          posted_before: dt.datetime):
        params = {
            "marketplaceId": marketplace_id,
            "postedAfter": posted_after.isoformat(),
            "postedBefore": posted_before.isoformat(),
            "transactionStatus": "RELEASED",
        }
        while True:
            page = self.request("GET", "/finances/2024-06-19/transactions", params=params)
            payload = page.get("payload", page)
            yield payload
            token = payload.get("nextToken")
            if not token:
                return
            params = {"nextToken": token}

    def download_report_document(self, metadata: dict,
                                 *, max_bytes: int = MAX_REPORT_DOCUMENT_BYTES) -> dict:
        url = metadata.get("url")
        if not isinstance(url, str) or not url.startswith("https://"):
            raise AmazonAPIError("Amazon report document URL is invalid.")
        owned = self.client is None
        client = self.client or httpx.Client(timeout=60.0)
        try:
            response = client.get(url, headers={"user-agent": os.getenv(
                "SP_API_USER_AGENT", "AICommerceOperator/0.1")})
            if response.status_code >= 400:
                raise AmazonAPIError(
                    f"Amazon report document download failed ({response.status_code})."
                )
            content = response.content
            if len(content) > max_bytes:
                raise AmazonAPIError("Amazon report document exceeds the size limit.")
            if metadata.get("compressionAlgorithm") == "GZIP":
                try:
                    content = gzip.decompress(content)
                except (OSError, EOFError) as exc:
                    raise AmazonAPIError("Amazon report document is not valid GZIP.") from exc
            if len(content) > max_bytes:
                raise AmazonAPIError("Amazon report document exceeds the size limit.")
            try:
                payload = httpx.Response(200, content=content).json()
            except ValueError as exc:
                raise AmazonAPIError("Amazon report document is not valid JSON.") from exc
            if not isinstance(payload, dict):
                raise AmazonAPIError("Amazon report document has an invalid shape.")
            return payload
        finally:
            if owned:
                client.close()

    def paginate(self, path: str, *, params: dict | None = None,
                 token_param: str = "nextToken"):
        page_params = dict(params or {})
        while True:
            page = self.request("GET", path, params=page_params)
            yield page
            pagination = page.get("pagination") or page.get("payload", {}).get("pagination") or {}
            token = pagination.get("nextToken")
            if not token:
                return
            page_params[token_param] = token


def client_for_store(db: Session, *, store_id: uuid.UUID, region: str = "EU",
                     http_client: httpx.Client | None = None) -> SPAPIClient:
    refresh_token = credentials.load_credential(
        db, store_id=store_id, provider="amazon-sp-api"
    )
    if refresh_token is None:
        raise AmazonAuthorizationError("Amazon account is not connected.")
    return SPAPIClient(
        _access_token_for_store(store_id, refresh_token, client=http_client),
        region=region, client=http_client
    )
