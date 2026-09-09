"""
Shopify's GraphQL limit is a leaky bucket priced in query cost.

Two things make it easy to get wrong, and both are tested here: a throttle
arrives as HTTP 200 with a THROTTLED error rather than a 429, and retrying the
whole job restarts pagination — spending more budget than the attempt that was
throttled, so the retry deepens the problem it is meant to solve.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import httpx
import pytest

from app import shopify
from app.shopify import (THROTTLE_MAX_ATTEMPTS, THROTTLE_MAX_WAIT_SECONDS,
                         is_throttled, throttle_wait_seconds)

SHOP = "throttle-shop.myshopify.com"


@pytest.fixture(autouse=True)
def _config(monkeypatch):
    monkeypatch.setenv("SHOPIFY_CLIENT_ID", "id")
    monkeypatch.setenv("SHOPIFY_CLIENT_SECRET", "secret")
    monkeypatch.setenv("SHOPIFY_REDIRECT_URI", "https://app.test/cb")


def _throttled_body(*, requested=302, available=100, restore=50):
    return {
        "errors": [{"message": "Throttled", "extensions": {"code": "THROTTLED"}}],
        "extensions": {"cost": {
            "requestedQueryCost": requested,
            "throttleStatus": {"maximumAvailable": 1000,
                               "currentlyAvailable": available,
                               "restoreRate": restore},
        }},
    }


# --- the arithmetic ----------------------------------------------------------

def test_the_wait_comes_from_shopifys_own_numbers():
    """302 wanted, 100 available, 50 restored per second -> 4.04s plus margin."""
    assert throttle_wait_seconds(_throttled_body()["extensions"]) == pytest.approx(4.29)


def test_a_bucket_that_can_already_afford_it_does_not_wait_long():
    extensions = _throttled_body(requested=10, available=900)["extensions"]
    assert throttle_wait_seconds(extensions, default=2.0) == 2.0


def test_the_wait_is_capped_so_a_sync_cannot_stall_indefinitely():
    extensions = _throttled_body(requested=100000, available=0, restore=1)["extensions"]
    assert throttle_wait_seconds(extensions) == THROTTLE_MAX_WAIT_SECONDS


def test_missing_or_nonsense_numbers_fall_back_rather_than_dividing_by_zero():
    assert throttle_wait_seconds(None, default=1.5) == 1.5
    assert throttle_wait_seconds({}, default=1.5) == 1.5
    assert throttle_wait_seconds(
        {"cost": {"throttleStatus": {"restoreRate": 0}}}, default=1.5) == 1.5
    assert throttle_wait_seconds(
        {"cost": {"requestedQueryCost": "lots"}}, default=1.5) == 1.5


def test_only_a_throttle_error_counts_as_throttling():
    assert is_throttled(_throttled_body()["errors"])
    assert not is_throttled([{"message": "Field does not exist"}])
    assert not is_throttled([{"extensions": {"code": "ACCESS_DENIED"}}])
    assert not is_throttled([])


# --- the behaviour -----------------------------------------------------------

def _client(monkeypatch, handler):
    monkeypatch.setattr(shopify.time, "sleep", lambda seconds: waits.append(seconds))
    return httpx.Client(transport=httpx.MockTransport(handler))


waits: list[float] = []


@pytest.fixture(autouse=True)
def _reset_waits():
    waits.clear()


def test_a_throttled_request_is_waited_out_and_retried_not_failed(monkeypatch):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(200, json=_throttled_body())
        return httpx.Response(200, json={"data": {"shop": {"name": "ok"}}})

    with _client(monkeypatch, handler) as http:
        data = shopify.AdminGraphQLClient(SHOP, "t", client=http).execute("{shop}", {})

    assert data == {"shop": {"name": "ok"}}
    assert calls["n"] == 2
    assert waits == [pytest.approx(4.29)]      # it waited what Shopify asked for


def test_persistent_throttling_eventually_gives_up_with_useful_advice(monkeypatch):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(200, json=_throttled_body())

    with _client(monkeypatch, handler) as http:
        with pytest.raises(shopify.ShopifyAPIError) as exc:
            shopify.AdminGraphQLClient(SHOP, "t", client=http).execute("{shop}", {})

    assert calls["n"] == THROTTLE_MAX_ATTEMPTS
    assert "shorter range" in str(exc.value)


def test_pagination_resumes_instead_of_restarting_when_a_page_is_throttled(monkeypatch):
    """The point of handling this per request: page one is not fetched twice."""
    import datetime as dt

    seen: list[str | None] = []
    calls = {"n": 0}

    def handler(request):
        import json as _json
        cursor = _json.loads(request.content)["variables"].get("cursor")
        calls["n"] += 1
        if calls["n"] == 2:                     # throttle the second page only
            return httpx.Response(200, json=_throttled_body())
        seen.append(cursor)
        last = cursor is not None
        return httpx.Response(200, json={"data": {"orders": {
            "nodes": [{"createdAt": "2026-08-01T00:00:00Z", "cancelledAt": None,
                       "currencyCode": "USD",
                       "currentTotalPriceSet": {"shopMoney": {"amount": "10.00",
                                                              "currencyCode": "USD"}},
                       "lineItems": {"nodes": [{"quantity": 1}]}}],
            "pageInfo": {"hasNextPage": not last, "endCursor": "cursor-2"},
        }}})

    with _client(monkeypatch, handler) as http:
        pages = list(shopify.AdminGraphQLClient(SHOP, "t", client=http).order_pages(
            created_at_min=dt.datetime(2026, 8, 1, tzinfo=dt.timezone.utc)))

    assert len(pages) == 2
    assert seen == [None, "cursor-2"]          # page one fetched once, not twice
    assert len(waits) == 1


def test_a_rest_style_429_is_also_a_pause_not_a_failure(monkeypatch):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "3"})
        return httpx.Response(200, json={"data": {"ok": True}})

    with _client(monkeypatch, handler) as http:
        assert shopify.AdminGraphQLClient(SHOP, "t", client=http).execute("{x}", {}) == {"ok": True}
    assert waits == [3.0]


def test_a_revoked_token_is_not_mistaken_for_a_pause(monkeypatch):
    """401 must still stop immediately; waiting cannot un-revoke a token."""
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(401)

    with _client(monkeypatch, handler) as http:
        with pytest.raises(shopify.ShopifyAuthorizationError):
            shopify.AdminGraphQLClient(SHOP, "t", client=http).execute("{x}", {})

    assert calls["n"] == 1
    assert waits == []
