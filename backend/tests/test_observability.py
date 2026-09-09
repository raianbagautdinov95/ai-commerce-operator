"""
Tests for what leaves this process when something goes wrong.

Error reporting is the one path that is *designed* to take internal state
somewhere else, which makes it the worst place to be careless. `send_default_pii
= False` covers what the SDK collects on its own; it does nothing about the
strings this application composed itself, and this application composes strings
out of URLs.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import observability

SECRET = "sk-live-000-not-a-real-key"


# --- what must never reach Sentry -------------------------------------------

def test_a_key_in_an_exception_message_is_scrubbed():
    """How the Keepa key would have travelled: httpx names the URL it called."""
    event = observability.scrub_event({
        "exception": {"values": [
            {"type": "HTTPStatusError",
             "value": f"Server error for url 'https://api.keepa.com/search?key={SECRET}'"},
        ]},
    })
    assert SECRET not in str(event)


def test_oauth_parameters_in_a_breadcrumb_are_scrubbed():
    event = observability.scrub_event({
        "breadcrumbs": {"values": [
            {"message": "GET /api/integrations/shopify/callback?code=abc&hmac=def"},
        ]},
    })
    text = str(event)
    assert "abc" not in text and "def" not in text


def test_the_message_itself_is_scrubbed_in_both_shapes():
    plain = observability.scrub_event({"message": f"failed: ?token={SECRET}"})
    assert SECRET not in str(plain)
    formatted = observability.scrub_event(
        {"message": {"formatted": f"failed: ?token={SECRET}"}})
    assert SECRET not in str(formatted)


def test_request_body_query_and_cookies_are_dropped_entirely():
    event = observability.scrub_event({"request": {
        "data": {"email": "someone@example.test", "line_items": [{"sku": "A"}]},
        "query_string": f"code={SECRET}",
        "cookies": {"session": "abc"},
        "headers": {"Authorization": "Bearer x", "X-Shopify-Hmac-Sha256": "sig",
                    "User-Agent": "curl"},
    }})
    request = event["request"]
    assert "data" not in request and "query_string" not in request
    assert "cookies" not in request
    assert request["headers"]["Authorization"] == "[Filtered]"
    assert request["headers"]["X-Shopify-Hmac-Sha256"] == "[Filtered]"
    # Something has to survive or the report is useless.
    assert request["headers"]["User-Agent"] == "curl"


def test_who_the_user_was_is_removed():
    event = observability.scrub_event({"user": {
        "id": "store-1", "email": "seller@example.test",
        "username": "seller", "ip_address": "203.0.113.9"}})
    assert event["user"] == {"id": "store-1"}


def test_scrubbing_an_ordinary_event_changes_nothing_useful():
    """Over-scrubbing produces reports nobody can act on, which is its own
    failure."""
    event = observability.scrub_event({
        "message": "Commerce scan for a.myshopify.com: 1 finding(s), 1 opened",
    })
    assert "a.myshopify.com" in event["message"]
    assert "1 finding(s)" in event["message"]


# --- the half nobody watches a screen for -----------------------------------

def test_the_worker_initialises_error_reporting():
    """The API called initialize() at import and the worker never did, so a
    failing job was invisible: no screen, no alert, nothing but a log line in a
    container somebody would have to think to open."""
    import inspect

    from app import worker

    assert "observability.initialize()" in inspect.getsource(worker.main)


def test_reporting_is_off_unless_a_dsn_is_configured(monkeypatch):
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    assert observability.initialize() is False
    assert observability.status()["sentry_configured"] is False
