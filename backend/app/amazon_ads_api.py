"""
Amazon Advertising API — the daily-money channel.

PPC is the one place where a seller's money moves every single day and where a
mistake is measurable within a fortnight. Everything else the Operator watches
(listings, stock, product choice) changes on the scale of weeks.

Read path (implemented here):
    profiles -> request a Sponsored Products report -> poll -> download -> rows

Write path (`create_negative_keywords`): the narrowest useful change there is.
Adding a negative keyword stops spend on a term that was not converting; it does
not alter bids, budgets or targeting, and it is trivially reversible. It is the
right first thing to let an Operator do unattended — and it still goes through
`guardrails.evaluate` before it may run.

Authentication reuses the LWA flow in `amazon_sp_api`; only the scope differs
(`advertising::campaign_management`). Access tokens are cached per
(store, credential) by that module, so rotating the credential misses the cache.

NOTE ON VERIFICATION: unlike the Shopify path, this module has never run against
a live Amazon account — it needs an approved Advertising API application, which
needs a Professional seller account. Everything below is exercised against
recorded-shape mocks. Report column names and the v3 content types are the parts
most likely to need correction on first contact; they are isolated in
`SEARCH_TERM_COLUMNS` and `_CONTENT_TYPE` for exactly that reason.
"""
from __future__ import annotations

import datetime as dt
import gzip
import json
import os
import time
import uuid
from dataclasses import dataclass, field

import httpx
from sqlalchemy.orm import Session

from . import amazon_sp_api, credentials
from .change_verification import ChangeOutcome, verify_presence

CREDENTIAL_PROVIDER = "amazon-ads-api"
LWA_SCOPE = "advertising::campaign_management"

REGIONAL_ENDPOINTS = {
    "NA": "https://advertising-api.amazon.com",
    "EU": "https://advertising-api-eu.amazon.com",
    "FE": "https://advertising-api-fe.amazon.com",
}
RETRYABLE_STATUS = {429, 500, 502, 503, 504}

# v3 uses per-resource vendor content types rather than plain application/json.
_CONTENT_TYPE = {
    "report": "application/vnd.createasyncreportrequest.v3+json",
    "negative_keyword": "application/vnd.spnegativekeyword.v3+json",
}

# The columns the PPC engine needs to find wasted spend. Kept in one place: this
# is the first thing that will need correcting against the live API.
SEARCH_TERM_COLUMNS = [
    "searchTerm", "keyword", "keywordId", "matchType",
    "campaignId", "campaignName", "adGroupId",
    "impressions", "clicks", "cost", "purchases7d", "sales7d",
]

REPORT_TERMINAL = {"COMPLETED", "SUCCESS", "FAILURE", "CANCELLED"}
REPORT_FAILED = {"FAILURE", "CANCELLED"}


class AdsAuthorizationError(RuntimeError):
    pass


class AdsAPIError(RuntimeError):
    pass


class AdsReportTimeout(RuntimeError):
    pass


def validate_ads_config() -> None:
    missing = [name for name in ("ADS_API_CLIENT_ID", "ADS_API_CLIENT_SECRET")
               if not os.getenv(name)]
    if missing:
        raise RuntimeError("Missing Amazon Ads API configuration: " + ", ".join(missing))


@dataclass
class Profile:
    """One advertising account. Every request must name the profile it acts for."""
    profile_id: str
    country_code: str
    currency: str
    marketplace_id: str | None = None
    account_name: str | None = None


@dataclass
class SearchTermRow:
    """One search term's fortnight, in the shape the PPC engine already understands."""
    search_term: str
    keyword: str
    match_type: str
    campaign_id: str
    campaign_name: str
    keyword_id: str | None
    ad_group_id: str | None
    impressions: int
    clicks: int
    spend: float
    orders: int
    sales: float

    def to_keyword_input(self) -> dict:
        """Map onto ppc_engine's KeywordRequest shape."""
        return {"keyword": self.search_term, "clicks": self.clicks, "spend": self.spend,
                "sales": self.sales, "orders": self.orders,
                "impressions": self.impressions, "match_type": self.match_type or "broad"}


def _as_float(value) -> float:
    try:
        return round(float(value or 0), 4)
    except (TypeError, ValueError):
        return 0.0


def _as_int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _row(raw: dict) -> SearchTermRow | None:
    term = (raw.get("searchTerm") or "").strip()
    if not term:
        return None
    return SearchTermRow(
        search_term=term,
        keyword=(raw.get("keyword") or "").strip(),
        match_type=(raw.get("matchType") or "").strip().lower(),
        campaign_id=str(raw.get("campaignId") or ""),
        campaign_name=(raw.get("campaignName") or "").strip(),
        keyword_id=str(raw["keywordId"]) if raw.get("keywordId") is not None else None,
        ad_group_id=str(raw["adGroupId"]) if raw.get("adGroupId") is not None else None,
        impressions=_as_int(raw.get("impressions")),
        clicks=_as_int(raw.get("clicks")),
        spend=_as_float(raw.get("cost")),
        orders=_as_int(raw.get("purchases7d")),
        sales=_as_float(raw.get("sales7d")),
    )


def parse_search_term_rows(payload: list[dict]) -> list[SearchTermRow]:
    """Public so a live report can be replayed through it without a network call."""
    return [row for row in (_row(raw) for raw in payload) if row is not None]


@dataclass
class AdsClient:
    access_token: str
    region: str = "EU"
    profile_id: str | None = None
    client: httpx.Client | None = None
    max_attempts: int = 4

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1.")

    def _headers(self, content_type: str | None = None) -> dict[str, str]:
        validate_ads_config()
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Amazon-Advertising-API-ClientId": os.environ["ADS_API_CLIENT_ID"],
        }
        if self.profile_id:
            headers["Amazon-Advertising-API-Scope"] = str(self.profile_id)
        if content_type:
            headers["Content-Type"] = content_type
            headers["Accept"] = content_type
        return headers

    def request(self, method: str, path: str, *, json_body: dict | None = None,
                content_type: str | None = None, retry_safe: bool = False) -> dict:
        endpoint = REGIONAL_ENDPOINTS.get(self.region.upper())
        if endpoint is None:
            raise ValueError("Unsupported Ads API region.")
        owned = self.client is None
        client = self.client or httpx.Client(timeout=60.0)
        try:
            for attempt in range(self.max_attempts):
                response = client.request(method, endpoint + path, json=json_body,
                                          headers=self._headers(content_type))
                # Writes are never replayed unless explicitly declared safe.
                may_retry = method.upper() in {"GET", "HEAD"} or retry_safe
                if response.status_code not in RETRYABLE_STATUS or not may_retry:
                    break
                if attempt + 1 < self.max_attempts:
                    delay = min(float(response.headers.get("Retry-After", 2 ** attempt)), 30.0)
                    time.sleep(max(delay, 0.0))
            if response.status_code == 401:
                raise AdsAuthorizationError("Amazon Ads API rejected the credentials.")
            if response.status_code >= 400:
                request_id = response.headers.get("x-amzn-RequestId", "unknown")
                raise AdsAPIError(
                    f"Amazon Ads API request failed ({response.status_code}, "
                    f"request_id={request_id})."
                )
            if not response.content:
                return {}
            return response.json()
        finally:
            if owned:
                client.close()

    # --- read -------------------------------------------------------------

    def profiles(self) -> list[Profile]:
        payload = self.request("GET", "/v2/profiles")
        rows = payload if isinstance(payload, list) else payload.get("profiles", [])
        found = []
        for raw in rows:
            account = raw.get("accountInfo") or {}
            found.append(Profile(
                profile_id=str(raw.get("profileId")),
                country_code=raw.get("countryCode", ""),
                currency=raw.get("currencyCode", ""),
                marketplace_id=account.get("marketplaceStringId"),
                account_name=account.get("name"),
            ))
        return found

    def request_search_term_report(self, *, start: dt.date, end: dt.date) -> str:
        """Ask for a Sponsored Products search-term report. Returns its id."""
        body = {
            "name": f"aco-search-term-{start:%Y%m%d}-{end:%Y%m%d}",
            "startDate": start.isoformat(),
            "endDate": end.isoformat(),
            "configuration": {
                "adProduct": "SPONSORED_PRODUCTS",
                "groupBy": ["searchTerm"],
                "columns": SEARCH_TERM_COLUMNS,
                "reportTypeId": "spSearchTerm",
                "timeUnit": "SUMMARY",
                "format": "GZIP_JSON",
            },
        }
        payload = self.request("POST", "/reporting/reports", json_body=body,
                               content_type=_CONTENT_TYPE["report"])
        report_id = payload.get("reportId")
        if not report_id:
            raise AdsAPIError("Amazon Ads API did not return a report id.")
        return str(report_id)

    def report_status(self, report_id: str) -> dict:
        return self.request("GET", f"/reporting/reports/{report_id}")

    def await_report(self, report_id: str, *, timeout_s: float = 900.0,
                     poll_s: float = 20.0) -> str:
        """Poll until the report is ready and return its download URL."""
        deadline = time.monotonic() + timeout_s
        while True:
            status_payload = self.report_status(report_id)
            status = str(status_payload.get("status", "")).upper()
            if status in REPORT_FAILED:
                raise AdsAPIError(
                    f"Amazon could not produce the report ({status}): "
                    f"{status_payload.get('failureReason') or 'no reason given'}"
                )
            if status in REPORT_TERMINAL:
                url = status_payload.get("url") or status_payload.get("location")
                if not url:
                    raise AdsAPIError("Report finished without a download URL.")
                return str(url)
            if time.monotonic() >= deadline:
                raise AdsReportTimeout(
                    f"Report {report_id} was still {status or 'pending'} after "
                    f"{timeout_s:.0f}s."
                )
            time.sleep(poll_s)

    def download_report(self, url: str) -> list[SearchTermRow]:
        """Fetch the finished report. It is gzipped JSON on a presigned URL."""
        owned = self.client is None
        client = self.client or httpx.Client(timeout=120.0)
        try:
            # A presigned URL carries its own auth; sending ours would break it.
            response = client.get(url)
            if response.status_code >= 400:
                raise AdsAPIError(f"Could not download the report ({response.status_code}).")
            body = response.content
            if body[:2] == b"\x1f\x8b":
                body = gzip.decompress(body)
            payload = json.loads(body.decode("utf-8"))
        finally:
            if owned:
                client.close()
        if isinstance(payload, dict):
            payload = payload.get("rows") or payload.get("data") or []
        return parse_search_term_rows(payload)

    def search_terms(self, *, days: int = 14, timeout_s: float = 900.0,
                     poll_s: float = 20.0) -> list[SearchTermRow]:
        """The whole read path, end to end."""
        end = dt.date.today() - dt.timedelta(days=1)      # yesterday: today is incomplete
        start = end - dt.timedelta(days=max(days, 1) - 1)
        report_id = self.request_search_term_report(start=start, end=end)
        url = self.await_report(report_id, timeout_s=timeout_s, poll_s=poll_s)
        return self.download_report(url)

    # --- write ------------------------------------------------------------

    def create_negative_keywords(self, entries: list[dict]) -> dict:
        """Stop spend on terms that are not converting. The narrowest useful write.

        Each entry needs campaignId, adGroupId, keywordText and matchType
        (NEGATIVE_EXACT or NEGATIVE_PHRASE). Never retried: a replayed create
        would duplicate negatives.
        """
        if not entries:
            return {"negativeKeywords": []}
        for entry in entries:
            missing = [k for k in ("campaignId", "adGroupId", "keywordText", "matchType")
                       if not entry.get(k)]
            if missing:
                raise ValueError(f"Negative keyword is missing {', '.join(missing)}")
        return self.request(
            "POST", "/sp/negativeKeywords",
            json_body={"negativeKeywords": entries},
            content_type=_CONTENT_TYPE["negative_keyword"],
            retry_safe=False,
        )


    def list_negative_keywords(self, *, campaign_id: str, ad_group_id: str) -> list[dict]:
        """Read the negatives currently on one ad group. Both halves of the bracket."""
        payload = self.request(
            "POST", "/sp/negativeKeywords/list",
            json_body={"campaignIdFilter": {"include": [str(campaign_id)]},
                       "adGroupIdFilter": {"include": [str(ad_group_id)]},
                       "maxResults": 500},
            content_type=_CONTENT_TYPE["negative_keyword"],
            retry_safe=True,      # a list is a read; replaying it changes nothing
        )
        return payload.get("negativeKeywords") or []

    def apply_negative_keyword(self, *, campaign_id: str, ad_group_id: str,
                               keyword_text: str,
                               match_type: str = "NEGATIVE_EXACT") -> ChangeOutcome:
        """Add one negative keyword and prove it landed.

        Read, mutate, read again. A 200 from the create is not evidence: the
        account is the evidence. If the term was already negated, this reports
        that nothing here is ours rather than claiming the saving.
        """
        def texts(rows: list[dict]) -> list[str]:
            return [str(r.get("keywordText", "")) for r in rows]

        before = texts(self.list_negative_keywords(
            campaign_id=campaign_id, ad_group_id=ad_group_id))
        if str(keyword_text).strip().lower() in {t.strip().lower() for t in before}:
            # Precondition already fails; do not spend a write to find that out.
            return verify_presence(expected=keyword_text, before=before, after=before)

        created = self.create_negative_keywords([{
            "campaignId": str(campaign_id), "adGroupId": str(ad_group_id),
            "keywordText": keyword_text, "matchType": match_type,
        }])
        external_id = None
        for entry in (created.get("negativeKeywords") or []):
            external_id = str(entry.get("negativeKeywordId") or entry.get("keywordId") or "") or None

        try:
            after = texts(self.list_negative_keywords(
                campaign_id=campaign_id, ad_group_id=ad_group_id))
        except AdsAPIError:
            # The write may well have worked; we simply cannot show it.
            return verify_presence(expected=keyword_text, before=before, after=[],
                                   external_id=external_id, read_back_failed=True)
        return verify_presence(expected=keyword_text, before=before, after=after,
                               external_id=external_id)


def client_for_store(db: Session, *, store_id: uuid.UUID, region: str = "EU",
                     profile_id: str | None = None,
                     http_client: httpx.Client | None = None) -> AdsClient:
    refresh_token = credentials.load_credential(
        db, store_id=store_id, provider=CREDENTIAL_PROVIDER
    )
    if refresh_token is None:
        raise AdsAuthorizationError("The Amazon advertising account is not connected.")
    access_token = amazon_sp_api._access_token_for_store(
        store_id, refresh_token, client=http_client
    )
    return AdsClient(access_token, region=region, profile_id=profile_id,
                     client=http_client)
